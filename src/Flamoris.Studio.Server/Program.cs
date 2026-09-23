using System.Threading.RateLimiting;
using Flamoris.Logging;
using Flamoris.Studio.Server.Access;
using Flamoris.Studio.Server.Assets;
using Flamoris.Studio.Server.Data;
using Flamoris.Studio.Server.Executions;
using Flamoris.Studio.Server.Generation;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 128 * 1024);
var connection = builder.Configuration.GetConnectionString("Studio");
if (string.IsNullOrWhiteSpace(connection))
    throw new InvalidOperationException("ConnectionStrings:Studio must be configured outside the repository.");
builder.Services.AddDbContext<StudioDbContext>(options => options.UseNpgsql(connection));
builder.Services.AddIdentityCore<StudioUser>(options =>
    {
        options.Password.RequiredLength = 12;
        options.Password.RequireDigit = true;
        options.Password.RequireUppercase = true;
        options.Lockout.DefaultLockoutTimeSpan = TimeSpan.FromMinutes(15);
        options.Lockout.MaxFailedAccessAttempts = 5;
        options.User.RequireUniqueEmail = true;
    })
    .AddRoles<IdentityRole>()
    .AddEntityFrameworkStores<StudioDbContext>()
    .AddSignInManager()
    .AddDefaultTokenProviders();
builder.Services.AddAuthentication(IdentityConstants.ApplicationScheme)
    .AddIdentityCookies();
builder.Services.ConfigureApplicationCookie(options =>
{
    options.Cookie.Name = "flamoris.studio";
    options.Cookie.HttpOnly = true;
    options.Cookie.SameSite = SameSiteMode.Strict;
    options.Cookie.SecurePolicy = builder.Environment.IsDevelopment()
        ? CookieSecurePolicy.SameAsRequest : CookieSecurePolicy.Always;
    options.LoginPath = "/api/auth/login";
    options.Events.OnRedirectToLogin = context =>
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        return Task.CompletedTask;
    };
    options.Events.OnRedirectToAccessDenied = context =>
    {
        context.Response.StatusCode = StatusCodes.Status403Forbidden;
        return Task.CompletedTask;
    };
});

var keyDir = builder.Configuration["Identity:DataProtectionDirectory"];
if (!builder.Environment.IsDevelopment() && string.IsNullOrWhiteSpace(keyDir))
    throw new InvalidOperationException("Identity:DataProtectionDirectory is required to persist session keys.");
if (!string.IsNullOrWhiteSpace(keyDir))
{
    Directory.CreateDirectory(keyDir);
    builder.Services.AddDataProtection().SetApplicationName("Flamoris.Studio")
        .PersistKeysToFileSystem(new DirectoryInfo(keyDir));
}
builder.Services.AddAntiforgery(options =>
{
    options.HeaderName = "X-CSRF-TOKEN";
    options.Cookie.Name = "flamoris.studio.csrf";
    options.Cookie.HttpOnly = true;
    options.Cookie.SameSite = SameSiteMode.Strict;
    options.Cookie.SecurePolicy = builder.Environment.IsDevelopment()
        ? CookieSecurePolicy.SameAsRequest : CookieSecurePolicy.Always;
});
builder.Services.AddAuthorization();
builder.Services.AddRateLimiter(options => options.AddPolicy("auth", context =>
    RateLimitPartition.GetFixedWindowLimiter(context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
        _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = 10, Window = TimeSpan.FromMinutes(5), QueueLimit = 0
        })));
builder.Services.AddScoped<IGenerationGateway, McpGenerationGateway>();
builder.Services.AddScoped<StudioExecutionService>();
builder.Services.AddSingleton<ThumbnailStore>();
builder.Services.AddSingleton(FlamorisLogger.Create(new LoggingOptions
{
    Level = builder.Configuration["Logging:Level"] ?? "info",
    Outputs = [new LogOutputOptions { Type = "console" }]
}));

var app = builder.Build();
app.Services.GetRequiredService<FlamorisLogger>().Info("studio.lifecycle", "Studio process starting");
app.UseHttpsRedirection();
app.UseStaticFiles();
app.UseAuthentication();
app.UseAuthorization();
app.UseRateLimiter();
app.UseAntiforgery();
app.Use(async (context, next) =>
{
    // Antiforgery is explicitly validated for JSON writes, including login/registration.
    if (context.Request.Path.StartsWithSegments("/api") &&
        HttpMethods.IsPost(context.Request.Method))
    {
        try { await context.RequestServices.GetRequiredService<IAntiforgery>().ValidateRequestAsync(context); }
        catch (AntiforgeryValidationException)
        {
            context.Response.StatusCode = StatusCodes.Status400BadRequest;
            return;
        }
    }
    await next();
});

app.MapGet("/api/session", (HttpContext context, IAntiforgery antiforgery) =>
{
    context.Response.Headers.CacheControl = "no-store";
    var token = antiforgery.GetAndStoreTokens(context).RequestToken;
    return Results.Ok(new { authenticated = context.User.Identity?.IsAuthenticated == true,
        userName = context.User.Identity?.IsAuthenticated == true ? context.User.Identity.Name : null,
        csrfToken = token });
});

app.MapGet("/api/system/status", () => Results.Ok(new { healthy = true, service = "studio" }));
app.MapPost("/api/auth/register", async (Credentials input, UserManager<StudioUser> manager,
    SignInManager<StudioUser> signIn, IConfiguration config) =>
{
    if (!config.GetValue<bool>("Identity:AllowRegistration")) return Results.NotFound();
    if (string.IsNullOrWhiteSpace(input.Email) || input.Email.Length > 256 ||
        input.Password is null || input.Password.Length > 256) return Results.BadRequest();
    var user = new StudioUser { UserName = input.Email, Email = input.Email };
    var created = await manager.CreateAsync(user, input.Password);
    if (!created.Succeeded) return Results.BadRequest(new { error = "Unable to create account." });
    await signIn.SignInAsync(user, isPersistent: false);
    return Results.Ok(new { userName = user.UserName });
}).RequireRateLimiting("auth");

app.MapPost("/api/auth/login", async (Credentials input, SignInManager<StudioUser> signIn) =>
{
    if (string.IsNullOrWhiteSpace(input.Email) || input.Email.Length > 256 ||
        input.Password is null || input.Password.Length > 256) return Results.BadRequest();
    var result = await signIn.PasswordSignInAsync(input.Email, input.Password,
        isPersistent: false, lockoutOnFailure: true);
    return result.Succeeded ? Results.Ok() : Results.Unauthorized();
}).RequireRateLimiting("auth");

app.MapPost("/api/auth/logout", async (SignInManager<StudioUser> signIn) =>
{
    await signIn.SignOutAsync();
    return Results.Ok();
}).RequireAuthorization();

var api = app.MapGroup("/api").RequireAuthorization();
api.MapGet("/generation/image/discovery", async (IGenerationGateway gateway, CancellationToken ct) =>
{
    var capability = await gateway.GetImageCapability(ct);
    if (!capability.Available) return Results.Ok(new { available = false, templates = Array.Empty<string>(),
        checkpoints = Array.Empty<GenerationModel>(), loras = Array.Empty<GenerationModel>() });
    var models = await gateway.GetModels("checkpoint", ct);
    var loras = await gateway.GetModels("lora", ct);
    return Results.Ok(new { available = true, templates = capability.Templates, checkpoints = models, loras });
});
api.MapPost("/generation/image/jobs", async (ImageRequest request, HttpContext context,
    StudioExecutionService executions, CancellationToken ct) =>
{
    if (!ImageValidation.Valid(request)) return Results.BadRequest(new { error = "Invalid image parameters." });
    var result = await executions.Submit(StudioUserId.From(context.User), request, ct);
    return Results.Created($"/api/executions/{result.Id}", result);
});
api.MapGet("/executions/{id:guid}", async (Guid id, HttpContext context,
    StudioExecutionService executions, CancellationToken ct) =>
{
    var view = await executions.Status(id, StudioUserId.From(context.User), ct);
    return view is null ? Results.NotFound() : Results.Ok(view);
});
api.MapGet("/executions/{id:guid}/result", async (Guid id, HttpContext context,
    StudioExecutionService executions, CancellationToken ct) =>
{
    var view = await executions.Result(id, StudioUserId.From(context.User), ct);
    return view is null ? Results.NotFound() : Results.Ok(view);
});
api.MapPost("/executions/{id:guid}/cancel", async (Guid id, HttpContext context,
    StudioExecutionService executions, CancellationToken ct) =>
{
    var view = await executions.Cancel(id, StudioUserId.From(context.User), ct);
    return view is null ? Results.NotFound() : Results.Ok(view);
});
api.MapGet("/executions/{id:guid}/assets/{assetId:guid}/thumbnail", async (
    Guid id, Guid assetId, HttpContext context, StudioExecutionService executions, CancellationToken ct) =>
{
    var bytes = await executions.Thumbnail(id, assetId, StudioUserId.From(context.User), ct);
    context.Response.Headers.CacheControl = "private, no-store";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    return bytes is null ? Results.NotFound() : Results.File(bytes, "image/webp");
});
api.MapGet("/executions/{id:guid}/assets/{assetId:guid}/content", async (
    Guid id, Guid assetId, HttpContext context, StudioExecutionService executions, CancellationToken ct) =>
{
    var content = await executions.Asset(id, assetId, StudioUserId.From(context.User), ct);
    context.Response.Headers.CacheControl = "private, no-store";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    context.Response.Headers["Content-Security-Policy"] = "default-src 'none'; sandbox";
    return content is null ? Results.NotFound() : Results.File(content.Value.Bytes, content.Value.Mime);
});
api.MapGet("/executions/{id:guid}/assets/{assetId:guid}/download", async (
    Guid id, Guid assetId, HttpContext context, StudioExecutionService executions, CancellationToken ct) =>
{
    var content = await executions.Asset(id, assetId, StudioUserId.From(context.User), ct);
    context.Response.Headers.CacheControl = "private, no-store";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    return content is null ? Results.NotFound() :
        Results.File(content.Value.Bytes, content.Value.Mime, content.Value.Filename);
});

app.Use(async (context, next) =>
{
    try { await next(); }
    catch (GatewayException ex)
    {
        if (context.Response.HasStarted) throw;
        var (status, code, message) = ex.Category switch
        {
            GatewayError.Busy => (409, "busy", "Generation service is busy."),
            GatewayError.Validation => (400, "validation", "Generation request was rejected."),
            GatewayError.NotFound => (404, "not_found", "Generation reference is unavailable."),
            GatewayError.Unavailable => (503, "unavailable", "Generation service is unavailable."),
            _ => (502, "upstream_failure", "Generation service failed.")
        };
        context.Response.StatusCode = status;
        await context.Response.WriteAsJsonAsync(new { error = code, message });
    }
});

app.MapFallback("/api/{**path}", () => Results.NotFound());
app.MapFallbackToFile("index.html");
app.Run();

public sealed record Credentials(string Email, string Password);
public partial class Program { }
