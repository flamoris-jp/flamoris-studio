using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Flamoris.Studio.Server.Assets;
using Flamoris.Studio.Server.Data;
using Flamoris.Studio.Server.Generation;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;
using Xunit;

namespace Flamoris.Studio.Server.Tests;

[CollectionDefinition("postgres", DisableParallelization = true)]
public sealed class PostgreSqlCollection;

public sealed class FakeGeneration : IGenerationGateway
{
    public int Submits { get; private set; }
    public bool Busy { get; set; }
    public bool AmbiguousSubmit { get; set; }
    public byte[] Image { get; set; } = SamplePng();
    private static byte[] SamplePng()
    {
        using var image = new Image<Rgba32>(2, 2);
        using var stream = new MemoryStream();
        image.SaveAsPng(stream);
        return stream.ToArray();
    }
    public Task<GenerationCapability> GetImageCapability(CancellationToken ct) =>
        Task.FromResult(new GenerationCapability(true, ["text-to-image", "text-to-image-lora"]));
    public Task<IReadOnlyList<GenerationModel>> GetModels(string kind, CancellationToken ct) =>
        Task.FromResult<IReadOnlyList<GenerationModel>>(kind == "checkpoint"
            ? [new("checkpoint:base.safetensors", "base.safetensors")]
            : [new("lora:style.safetensors", "style.safetensors")]);
    public Task<string> BuildWorkflow(string template, object parameters, CancellationToken ct) =>
        Task.FromResult("workflow");
    public Task<GenerationJob> Submit(string workflowId, CancellationToken ct)
    {
        Submits++;
        if (Busy) throw new GatewayException(GatewayError.Busy);
        if (AmbiguousSubmit) throw new GatewayException(GatewayError.Unavailable);
        return Task.FromResult(new GenerationJob("private-upstream-job", "queued"));
    }
    public Task<GenerationJob> Status(string jobId, CancellationToken ct) =>
        Task.FromResult(new GenerationJob(jobId, "completed"));
    public Task<GenerationJob> Result(string jobId, CancellationToken ct) => Status(jobId, ct);
    public Task<GenerationJob> Cancel(string jobId, CancellationToken ct) => Status(jobId, ct);
    public Task<IReadOnlyList<GenerationAsset>> ListAssets(string jobId, CancellationToken ct) =>
        Task.FromResult<IReadOnlyList<GenerationAsset>>([new("private-upstream-asset", "../../other-user.png", "image", "image/png", Image.Length)]);
    public Task<GenerationContent> GetAsset(string assetId, CancellationToken ct) =>
        Task.FromResult(new GenerationContent(Image, "image/png"));
}

public sealed class StudioFactory(long maxAssetBytes = 67108864) : WebApplicationFactory<Program>
{
    public FakeGeneration Fake { get; } = new();
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Development");
        builder.UseContentRoot(Path.GetFullPath(Path.Combine(AppContext.BaseDirectory,
            "../../../../src/Flamoris.Studio.Server")));
        builder.UseSetting("Identity:AllowRegistration", "true");
        builder.UseSetting("Assets:MaxBytes", maxAssetBytes.ToString());
        builder.UseSetting("Assets:ThumbnailDirectory", Path.Combine(Path.GetTempPath(), "studio-test-" + Guid.NewGuid().ToString("N")));
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<IGenerationGateway>();
            services.AddSingleton<IGenerationGateway>(Fake);
        });
    }
}

[Collection("postgres")]
public sealed class StudioTests
{
    private static async Task<string> Token(HttpClient client) =>
        (await client.GetFromJsonAsync<JsonElement>("/api/session")).GetProperty("csrfToken").GetString()!;

    private static async Task Register(HttpClient client, string email)
    {
        var token = await Token(client);
        using var request = new HttpRequestMessage(HttpMethod.Post, "/api/auth/register")
        { Content = JsonContent.Create(new { email, password = "TestPassword123!" }) };
        request.Headers.Add("X-CSRF-TOKEN", token);
        Assert.Equal(HttpStatusCode.OK, (await client.SendAsync(request)).StatusCode);
    }

    private static async Task<HttpResponseMessage> Post(HttpClient client, string path, string token, object body)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, path) { Content = JsonContent.Create(body) };
        request.Headers.Add("X-CSRF-TOKEN", token);
        return await client.SendAsync(request);
    }

    private static object Sample => new
    {
        positivePrompt = "flowers", negativePrompt = "", checkpoint = "base.safetensors",
        width = 512, height = 512, steps = 20, cfg = 7.0, seed = 0, loras = Array.Empty<object>()
    };

    [Fact]
    public async Task UserOwnershipAndMediaAccessAreEnforced()
    {
        using var factory = new StudioFactory();
        using (var scope = factory.Services.CreateScope())
            await scope.ServiceProvider.GetRequiredService<StudioDbContext>().Database.MigrateAsync();
        using var owner = factory.CreateClient();
        using var stranger = factory.CreateClient();
        Assert.Equal(HttpStatusCode.Unauthorized, (await stranger.GetAsync("/api/generation/image/discovery")).StatusCode);
        await Register(owner, "owner-" + Guid.NewGuid().ToString("N") + "@example.test");
        await Register(stranger, "stranger-" + Guid.NewGuid().ToString("N") + "@example.test");
        Assert.Equal(HttpStatusCode.BadRequest, (await owner.PostAsJsonAsync("/api/generation/image/jobs", Sample)).StatusCode); // CSRF
        var submitted = await Post(owner, "/api/generation/image/jobs", await Token(owner), Sample);
        Assert.Equal(HttpStatusCode.Created, submitted.StatusCode);
        var id = (await submitted.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetString();
        Assert.NotNull(id);
        var path = $"/api/executions/{id}";
        var resolved = await owner.GetFromJsonAsync<JsonElement>(path + "/result");
        var raw = resolved.GetRawText();
        Assert.DoesNotContain("private-upstream-job", raw);
        Assert.DoesNotContain("private-upstream-asset", raw);
        Assert.DoesNotContain("../../", raw);
        Assert.DoesNotContain("flowers", raw); // private request snapshot
        var asset = resolved.GetProperty("assets")[0];
        Assert.Equal("other-user.png", asset.GetProperty("displayName").GetString());
        var assetId = asset.GetProperty("id").GetString();
        Assert.Equal(HttpStatusCode.NotFound, (await stranger.GetAsync(path)).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await stranger.GetAsync(path + "/result")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await Post(stranger, path + "/cancel", await Token(stranger), new { })).StatusCode);
        foreach (var suffix in new[] { "content", "download", "thumbnail" })
            Assert.Equal(HttpStatusCode.NotFound, (await stranger.GetAsync($"{path}/assets/{assetId}/{suffix}")).StatusCode);
        Assert.Equal(HttpStatusCode.OK, (await owner.GetAsync($"{path}/assets/{assetId}/content")).StatusCode);
        Assert.Equal("image/png", (await owner.GetAsync($"{path}/assets/{assetId}/content")).Content.Headers.ContentType?.MediaType);
        var download = await owner.GetAsync($"{path}/assets/{assetId}/download");
        Assert.Equal(HttpStatusCode.OK, download.StatusCode);
        Assert.Equal("other-user.png", download.Content.Headers.ContentDisposition?.FileNameStar ?? download.Content.Headers.ContentDisposition?.FileName);
        Assert.Equal(HttpStatusCode.OK, (await owner.GetAsync($"{path}/assets/{assetId}/thumbnail")).StatusCode);
        using (var scope = factory.Services.CreateScope())
        {
            var db = scope.ServiceProvider.GetRequiredService<StudioDbContext>();
            var record = await db.Assets.SingleAsync(x => x.Id.ToString() == assetId);
            Assert.NotNull(record.ThumbnailLocator);
            Assert.Equal("private-upstream-asset", record.StorageLocator);
        }
        Assert.Equal(HttpStatusCode.OK, (await owner.GetAsync(path)).StatusCode); // another scope, persisted lookup
    }

    [Fact]
    public void FilenameAndMediaSniffingAreBounded()
    {
        Assert.Equal("image.png", AssetSafety.Filename("C:\\private\\image.png"));
        Assert.False(AssetSafety.ValidImage([1, 2, 3], "image/png"));
        Assert.False(AssetSafety.ValidImage([137, 80, 78, 71], "text/html"));
        Assert.False(ImageValidation.Valid(new ImageRequest("x", "", 513, 512, 20, 7, 0, "base.safetensors", [])));
    }

    [Fact]
    public async Task BusyAndAmbiguousSubmitDoNotRetryOrRevealUpstreamIdentity()
    {
        using var factory = new StudioFactory();
        using (var scope = factory.Services.CreateScope())
            await scope.ServiceProvider.GetRequiredService<StudioDbContext>().Database.MigrateAsync();
        using var client = factory.CreateClient();
        var email = "submit-" + Guid.NewGuid().ToString("N") + "@example.test";
        await Register(client, email);
        factory.Fake.Busy = true;
        var busy = await Post(client, "/api/generation/image/jobs", await Token(client), Sample);
        Assert.Equal(HttpStatusCode.Conflict, busy.StatusCode);
        Assert.DoesNotContain("private-upstream", await busy.Content.ReadAsStringAsync());
        factory.Fake.Busy = false;
        factory.Fake.AmbiguousSubmit = true;
        var unknown = await Post(client, "/api/generation/image/jobs", await Token(client), Sample);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, unknown.StatusCode);
        Assert.Equal(2, factory.Fake.Submits); // exactly one per user action
        using var scope2 = factory.Services.CreateScope();
        var catalog = scope2.ServiceProvider.GetRequiredService<StudioDbContext>();
        var userId = await catalog.Users.Where(x => x.Email == email).Select(x => x.Id).SingleAsync();
        var states = await catalog.Executions.Where(x => x.UserId == userId).Select(x => x.LastKnownStatus).ToListAsync();
        Assert.Equal(2, states.Count);
        Assert.Contains("submission_unknown", states);
    }

    [Fact]
    public async Task AssetSizeBoundRejectsPreviewAndDownload()
    {
        using var factory = new StudioFactory(32);
        using (var scope = factory.Services.CreateScope())
            await scope.ServiceProvider.GetRequiredService<StudioDbContext>().Database.MigrateAsync();
        using var client = factory.CreateClient();
        await Register(client, "limit-" + Guid.NewGuid().ToString("N") + "@example.test");
        var submitted = await Post(client, "/api/generation/image/jobs", await Token(client), Sample);
        var id = (await submitted.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("id").GetString();
        var result = await client.GetFromJsonAsync<JsonElement>($"/api/executions/{id}/result");
        var assetId = result.GetProperty("assets")[0].GetProperty("id").GetString();
        foreach (var action in new[] { "content", "download" })
            Assert.Equal(HttpStatusCode.BadRequest, (await client.GetAsync($"/api/executions/{id}/assets/{assetId}/{action}")).StatusCode);
    }
}
