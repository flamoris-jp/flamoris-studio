using System.Text.Json;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Identity.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore;

namespace Flamoris.Studio.Server.Data;

public sealed class StudioUser : IdentityUser { }

public sealed class StudioExecution
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string UserId { get; set; } = "";
    public string Source { get; set; } = "generation";
    public string Category { get; set; } = "image";
    public string Operation { get; set; } = "image.generate";
    public string Workflow { get; set; } = "";
    public string? UpstreamJobId { get; set; }
    public string RequestSnapshot { get; set; } = "{}";
    public string LastKnownStatus { get; set; } = "submitting";
    public DateTimeOffset SubmittedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset? StartedAt { get; set; }
    public DateTimeOffset? CompletedAt { get; set; }
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
    public List<StudioAsset> Assets { get; set; } = [];
}

public sealed class StudioAsset
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string UserId { get; set; } = "";
    public Guid ExecutionId { get; set; }
    public StudioExecution Execution { get; set; } = null!;
    public string Source { get; set; } = "generation";
    public string UpstreamAssetId { get; set; } = "";
    public string StorageProvider { get; set; } = "generation-mcp";
    public string StorageLocator { get; set; } = "";
    public string? StoragePath { get; set; }
    public string? OriginalFilename { get; set; }
    public string DisplayName { get; set; } = "image";
    public string MediaKind { get; set; } = "image";
    public string MimeType { get; set; } = "image/png";
    public long? SizeBytes { get; set; }
    public int? Width { get; set; }
    public int? Height { get; set; }
    public string? Checksum { get; set; }
    public string? ThumbnailLocator { get; set; }
    public string Availability { get; set; } = "available";
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
    public string Metadata { get; set; } = "{}";
}

public sealed class StudioDbContext(DbContextOptions<StudioDbContext> options) : IdentityDbContext<StudioUser>(options)
{
    public DbSet<StudioExecution> Executions => Set<StudioExecution>();
    public DbSet<StudioAsset> Assets => Set<StudioAsset>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);
        builder.Entity<StudioExecution>(entity =>
        {
            entity.ToTable("executions");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.UserId).IsRequired();
            entity.HasOne<StudioUser>().WithMany().HasForeignKey(x => x.UserId)
                .OnDelete(DeleteBehavior.Cascade);
            entity.Property(x => x.RequestSnapshot).HasColumnType("jsonb");
            entity.HasIndex(x => new { x.UserId, x.CreatedAt });
            entity.HasIndex(x => x.UpstreamJobId);
        });
        builder.Entity<StudioAsset>(entity =>
        {
            entity.ToTable("assets");
            entity.HasKey(x => x.Id);
            entity.Property(x => x.Metadata).HasColumnType("jsonb");
            entity.HasOne<StudioUser>().WithMany().HasForeignKey(x => x.UserId)
                .OnDelete(DeleteBehavior.Cascade);
            entity.HasOne(x => x.Execution).WithMany(x => x.Assets)
                .HasForeignKey(x => x.ExecutionId).OnDelete(DeleteBehavior.Cascade);
            entity.HasIndex(x => new { x.UserId, x.ExecutionId });
            entity.HasIndex(x => new { x.ExecutionId, x.UpstreamAssetId }).IsUnique();
        });
    }
}
