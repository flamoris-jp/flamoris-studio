using System.Text.Json;
using Flamoris.Studio.Server.Access;
using Flamoris.Studio.Server.Assets;
using Flamoris.Studio.Server.Data;
using Flamoris.Studio.Server.Generation;
using Microsoft.EntityFrameworkCore;

namespace Flamoris.Studio.Server.Executions;

public sealed record AssetView(Guid Id, string DisplayName, string MimeType, long? SizeBytes,
    bool HasThumbnail, string PreviewUrl, string DownloadUrl, string ThumbnailUrl);
public sealed record ExecutionView(Guid Id, string State, DateTimeOffset SubmittedAt,
    IReadOnlyList<AssetView> Assets);

public sealed class StudioExecutionService(
    StudioDbContext db, IGenerationGateway gateway, ThumbnailStore thumbnails, IConfiguration config)
{
    private static readonly HashSet<string> Terminal = ["completed", "failed", "cancelled"];
    private async Task<StudioExecution?> Owned(Guid id, StudioUserId user, CancellationToken ct) =>
        await db.Executions.Include(x => x.Assets)
            .SingleOrDefaultAsync(x => x.Id == id && x.UserId == user.Value, ct);

    private static ExecutionView View(StudioExecution execution) => new(execution.Id,
        execution.LastKnownStatus, execution.SubmittedAt,
        execution.Assets.OrderBy(x => x.CreatedAt).Select(x => new AssetView(x.Id,
            x.DisplayName, x.MimeType, x.SizeBytes, x.ThumbnailLocator is not null,
            $"/api/executions/{execution.Id}/assets/{x.Id}/content",
            $"/api/executions/{execution.Id}/assets/{x.Id}/download",
            $"/api/executions/{execution.Id}/assets/{x.Id}/thumbnail")).ToArray());

    public async Task<ExecutionView> Submit(StudioUserId user, ImageRequest request, CancellationToken ct)
    {
        var capability = await gateway.GetImageCapability(ct);
        if (!capability.Available) throw new GatewayException(GatewayError.Unavailable);
        var template = request.Loras is { Length: > 0 } ? "text-to-image-lora" : "text-to-image";
        if (!capability.Templates.Contains(template)) throw new GatewayException(GatewayError.Unavailable);
        var workflowId = await gateway.BuildWorkflow(template, ImageValidation.Parameters(request), ct);
        var execution = new StudioExecution
        {
            UserId = user.Value, Workflow = template,
            RequestSnapshot = JsonSerializer.Serialize(request),
            LastKnownStatus = "submitting"
        };
        db.Executions.Add(execution);
        await db.SaveChangesAsync(ct); // record uncertain submissions before the non-idempotent call
        try
        {
            var submitted = await gateway.Submit(workflowId, ct);
            execution.UpstreamJobId = submitted.JobId;
            execution.LastKnownStatus = submitted.Status;
            execution.UpdatedAt = DateTimeOffset.UtcNow;
            await db.SaveChangesAsync(CancellationToken.None);
        }
        catch (GatewayException ex) when (ex.Category == GatewayError.Busy)
        {
            execution.LastKnownStatus = "busy";
            execution.UpdatedAt = DateTimeOffset.UtcNow;
            await db.SaveChangesAsync(CancellationToken.None);
            throw;
        }
        catch
        {
            execution.LastKnownStatus = "submission_unknown";
            execution.UpdatedAt = DateTimeOffset.UtcNow;
            await db.SaveChangesAsync(CancellationToken.None);
            throw;
        }
        return View(execution);
    }

    public async Task<ExecutionView?> Status(Guid id, StudioUserId user, CancellationToken ct)
    {
        var execution = await Owned(id, user, ct);
        if (execution is null) return null;
        if (execution.UpstreamJobId is null) return View(execution);
        var job = await gateway.Status(execution.UpstreamJobId, ct);
        await SetStatus(execution, job.Status, ct);
        return View(execution);
    }

    public async Task<ExecutionView?> Result(Guid id, StudioUserId user, CancellationToken ct)
    {
        var execution = await Owned(id, user, ct);
        if (execution is null) return null;
        if (execution.UpstreamJobId is null) return View(execution);
        var job = await gateway.Status(execution.UpstreamJobId, ct);
        await SetStatus(execution, job.Status, ct);
        if (job.Status != "completed") return View(execution);

        var result = await gateway.Result(execution.UpstreamJobId, ct);
        await SetStatus(execution, result.Status, ct);
        if (result.Status != "completed") return View(execution);

        var listing = await gateway.ListAssets(execution.UpstreamJobId, ct);
        foreach (var item in listing.Take(64))
        {
            if (item.MediaKind != "image" || !new[] { "image/png", "image/jpeg", "image/webp" }.Contains(item.MimeType))
                continue;
            if (execution.Assets.Any(x => x.UpstreamAssetId == item.AssetId)) continue;
            var asset = new StudioAsset
            {
                UserId = user.Value, ExecutionId = execution.Id,
                UpstreamAssetId = item.AssetId, StorageLocator = item.AssetId,
                OriginalFilename = item.Filename, DisplayName = AssetSafety.Filename(item.Filename),
                MediaKind = item.MediaKind, MimeType = item.MimeType, SizeBytes = item.SizeBytes
            };
            db.Assets.Add(asset);
            execution.Assets.Add(asset);
        }
        await db.SaveChangesAsync(ct);

        foreach (var asset in execution.Assets.Where(x => x.ThumbnailLocator is null).Take(8))
        {
            try { await EnsureThumbnail(asset, ct); }
            catch (Exception) when (!ct.IsCancellationRequested) { /* Preview/download remain usable. */ }
        }
        await db.SaveChangesAsync(ct);
        return View(execution);
    }

    public async Task<ExecutionView?> Cancel(Guid id, StudioUserId user, CancellationToken ct)
    {
        var execution = await Owned(id, user, ct);
        if (execution is null) return null;
        if (execution.UpstreamJobId is null || Terminal.Contains(execution.LastKnownStatus)) return View(execution);
        var job = await gateway.Cancel(execution.UpstreamJobId, ct);
        await SetStatus(execution, job.Status, ct); // cancellation request is not terminal by itself
        return View(execution);
    }

    private async Task SetStatus(StudioExecution execution, string status, CancellationToken ct)
    {
        execution.LastKnownStatus = status;
        execution.UpdatedAt = DateTimeOffset.UtcNow;
        if (status == "running" && execution.StartedAt is null) execution.StartedAt = execution.UpdatedAt;
        if (Terminal.Contains(status) && execution.CompletedAt is null) execution.CompletedAt = execution.UpdatedAt;
        await db.SaveChangesAsync(ct);
    }

    private async Task<StudioAsset?> OwnedAsset(Guid id, Guid assetId, StudioUserId user, CancellationToken ct)
    {
        var execution = await Owned(id, user, ct);
        return execution?.Assets.SingleOrDefault(x => x.Id == assetId && x.UserId == user.Value);
    }

    private async Task<GenerationContent> Content(StudioAsset asset, CancellationToken ct)
    {
        var max = Math.Clamp(config.GetValue<long>("Assets:MaxBytes", 67108864), 1, 67108864);
        if (asset.SizeBytes is > 0 && asset.SizeBytes > max) throw new GatewayException(GatewayError.Validation);
        var content = await gateway.GetAsset(asset.UpstreamAssetId, ct);
        if (content.Bytes.LongLength is <= 0 || content.Bytes.LongLength > max ||
            content.MimeType != asset.MimeType || !AssetSafety.ValidImage(content.Bytes, content.MimeType))
            throw new GatewayException(GatewayError.Validation);
        asset.SizeBytes = content.Bytes.LongLength;
        asset.UpdatedAt = DateTimeOffset.UtcNow;
        await db.SaveChangesAsync(ct);
        return content;
    }

    private async Task EnsureThumbnail(StudioAsset asset, CancellationToken ct)
    {
        if (asset.ThumbnailLocator is not null) return;
        var content = await Content(asset, ct);
        asset.ThumbnailLocator = await thumbnails.Save(asset.Id, content.Bytes, ct);
        asset.UpdatedAt = DateTimeOffset.UtcNow;
        await db.SaveChangesAsync(ct);
    }

    public async Task<(byte[] Bytes, string Mime, string Filename)?> Asset(
        Guid id, Guid assetId, StudioUserId user, CancellationToken ct)
    {
        var asset = await OwnedAsset(id, assetId, user, ct);
        if (asset is null) return null;
        var content = await Content(asset, ct);
        return (content.Bytes, content.MimeType, AssetSafety.Filename(asset.DisplayName));
    }

    public async Task<byte[]?> Thumbnail(Guid id, Guid assetId, StudioUserId user, CancellationToken ct)
    {
        var asset = await OwnedAsset(id, assetId, user, ct);
        if (asset is null) return null;
        if (asset.ThumbnailLocator is null) await EnsureThumbnail(asset, ct);
        return asset.ThumbnailLocator is null ? null : await thumbnails.Load(asset.ThumbnailLocator, ct);
    }
}
