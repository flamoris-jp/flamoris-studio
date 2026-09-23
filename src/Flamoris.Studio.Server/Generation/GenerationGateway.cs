using System.Text.Json;
using ModelContextProtocol.Client;
using ModelContextProtocol.Protocol;

namespace Flamoris.Studio.Server.Generation;

public enum GatewayError { Unavailable, Busy, Validation, NotFound, UpstreamFailure }
public sealed class GatewayException(GatewayError category) : Exception("Generation service error.")
{
    public GatewayError Category { get; } = category;
}

public sealed record GenerationCapability(bool Available, string[] Templates);
public sealed record GenerationModel(string Id, string Name);
public sealed record GenerationJob(string JobId, string Status);
public sealed record GenerationAsset(string AssetId, string Filename, string MediaKind, string MimeType, long? SizeBytes);
public sealed record GenerationContent(byte[] Bytes, string MimeType);

public interface IGenerationGateway
{
    Task<GenerationCapability> GetImageCapability(CancellationToken ct);
    Task<IReadOnlyList<GenerationModel>> GetModels(string kind, CancellationToken ct);
    Task<string> BuildWorkflow(string template, object parameters, CancellationToken ct);
    Task<GenerationJob> Submit(string workflowId, CancellationToken ct);
    Task<GenerationJob> Status(string jobId, CancellationToken ct);
    Task<GenerationJob> Result(string jobId, CancellationToken ct);
    Task<GenerationJob> Cancel(string jobId, CancellationToken ct);
    Task<IReadOnlyList<GenerationAsset>> ListAssets(string jobId, CancellationToken ct);
    Task<GenerationContent> GetAsset(string assetId, CancellationToken ct);
}

public sealed class McpGenerationGateway(IConfiguration configuration) : IGenerationGateway
{
    // The MCP SDK stays entirely behind this gateway. One request has one bounded session;
    // no user-facing endpoint accepts arbitrary tool names or argument dictionaries.
    private async Task<CallToolResult> Call(string name, Dictionary<string, object?> args, CancellationToken ct)
    {
        var endpoint = configuration["Generation:Endpoint"];
        if (!Uri.TryCreate(endpoint, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttps && uri.Scheme != Uri.UriSchemeHttp))
            throw new GatewayException(GatewayError.Unavailable);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(45));
        try
        {
            var transport = new HttpClientTransport(new HttpClientTransportOptions
            {
                Endpoint = uri, TransportMode = HttpTransportMode.StreamableHttp,
                ConnectionTimeout = TimeSpan.FromSeconds(10), EnableStandaloneGetStream = false
            });
            await using var client = await McpClient.CreateAsync(transport, cancellationToken: timeout.Token);
            // In particular, jobs.submit must never be retried after an ambiguous timeout.
            var result = await client.CallToolAsync(name, args, cancellationToken: timeout.Token);
            if (result.IsError is true)
            {
                var error = result.Content.OfType<TextContentBlock>().FirstOrDefault()?.Text ?? "";
                var category = error.Contains("Generation is busy", StringComparison.OrdinalIgnoreCase)
                    ? GatewayError.Busy : error.Contains("Unknown", StringComparison.OrdinalIgnoreCase)
                    ? GatewayError.NotFound : GatewayError.Validation;
                throw new GatewayException(category); // never forward upstream text
            }
            return result;
        }
        catch (GatewayException) { throw; }
        catch (OperationCanceledException) when (ct.IsCancellationRequested) { throw; }
        catch (Exception) { throw new GatewayException(GatewayError.Unavailable); }
    }

    private static JsonElement Structured(CallToolResult result) => result.StructuredContent
        ?? throw new GatewayException(GatewayError.UpstreamFailure);
    private static string Required(JsonElement element, string key) =>
        element.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String &&
        !string.IsNullOrWhiteSpace(value.GetString()) ? value.GetString()! :
        throw new GatewayException(GatewayError.UpstreamFailure);
    private static Dictionary<string, object?> Args(string key, object value) => new() { [key] = value };

    public async Task<GenerationCapability> GetImageCapability(CancellationToken ct)
    {
        var response = Structured(await Call("capabilities.list", new(), ct));
        if (!response.TryGetProperty("capabilities", out var list) || list.ValueKind != JsonValueKind.Array)
            throw new GatewayException(GatewayError.UpstreamFailure);
        foreach (var item in list.EnumerateArray())
            if (Required(item, "id") == "image.generate")
                return new(item.GetProperty("available").GetBoolean(),
                    item.GetProperty("workflow_templates").EnumerateArray().Select(x => x.GetString() ?? "").ToArray());
        return new(false, []);
    }

    public async Task<IReadOnlyList<GenerationModel>> GetModels(string kind, CancellationToken ct)
    {
        var response = Structured(await Call("models.list", Args("kind", kind), ct));
        return response.GetProperty("models").EnumerateArray()
            .Select(x => new GenerationModel(Required(x, "id"), Required(x, "name"))).ToArray();
    }

    public async Task<string> BuildWorkflow(string template, object parameters, CancellationToken ct) =>
        Required(Structured(await Call("workflows.build", new() { ["template"] = template, ["parameters"] = parameters }, ct)), "workflow_id");

    private async Task<GenerationJob> Job(string tool, string key, string id, CancellationToken ct)
    {
        var value = Structured(await Call(tool, Args(key, id), ct));
        return new(Required(value, "job_id"), Required(value, "status"));
    }
    public Task<GenerationJob> Submit(string workflowId, CancellationToken ct) => Job("jobs.submit", "workflow_id", workflowId, ct);
    public Task<GenerationJob> Status(string jobId, CancellationToken ct) => Job("jobs.status", "job_id", jobId, ct);
    public Task<GenerationJob> Result(string jobId, CancellationToken ct) => Job("jobs.result", "job_id", jobId, ct);
    public Task<GenerationJob> Cancel(string jobId, CancellationToken ct) => Job("jobs.cancel", "job_id", jobId, ct);

    public async Task<IReadOnlyList<GenerationAsset>> ListAssets(string jobId, CancellationToken ct)
    {
        var value = Structured(await Call("assets.list", Args("job_id", jobId), ct));
        return value.GetProperty("assets").EnumerateArray().Select(item => new GenerationAsset(
            Required(item, "asset_id"), Required(item, "filename"), Required(item, "media_kind"),
            Required(item, "mime_type"), item.TryGetProperty("size_bytes", out var size) &&
            size.ValueKind == JsonValueKind.Number ? size.GetInt64() : null)).ToArray();
    }

    public async Task<GenerationContent> GetAsset(string assetId, CancellationToken ct)
    {
        var result = await Call("assets.get", Args("asset_id", assetId), ct);
        var image = result.Content.OfType<ImageContentBlock>().SingleOrDefault()
            ?? throw new GatewayException(GatewayError.UpstreamFailure);
        // The SDK has already decoded this MCP image block. The upstream caps
        // images at 64 MiB; Studio enforces its own stricter configured cap.
        var max = configuration.GetValue<long>("Assets:MaxBytes", 67108864);
        if (image.Data.Length > max)
            throw new GatewayException(GatewayError.Validation);
        return new(image.DecodedData.ToArray(), image.MimeType);
    }
}
