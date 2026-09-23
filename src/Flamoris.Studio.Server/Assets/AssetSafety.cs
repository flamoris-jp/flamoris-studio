using SkiaSharp;

namespace Flamoris.Studio.Server.Assets;

public static class AssetSafety
{
    public static string Filename(string? raw)
    {
        var leaf = (raw ?? "image").Replace('\\', '/').Split('/').Last();
        var cleaned = new string(leaf.Where(c => !char.IsControl(c) &&
            (char.IsLetterOrDigit(c) || c is '.' or '_' or '-')).Take(100).ToArray()).Trim('.');
        return string.IsNullOrWhiteSpace(cleaned) ? "image" : cleaned;
    }

    public static bool ValidImage(byte[] bytes, string mime)
    {
        var signature = mime switch
        {
            "image/png" => bytes.AsSpan().StartsWith([137, 80, 78, 71, 13, 10, 26, 10]),
            "image/jpeg" => bytes.AsSpan().StartsWith([255, 216, 255]),
            "image/webp" => bytes.Length >= 12 && bytes.AsSpan().StartsWith("RIFF"u8) &&
                bytes.AsSpan(8, 4).SequenceEqual("WEBP"u8),
            _ => false
        };
        if (!signature) return false;
        try
        {
            using var stream = new SKMemoryStream(bytes);
            using var codec = SKCodec.Create(stream);
            var info = codec?.Info;
            var expected = mime switch { "image/png" => SKEncodedImageFormat.Png,
                "image/jpeg" => SKEncodedImageFormat.Jpeg, _ => SKEncodedImageFormat.Webp };
            return codec?.EncodedFormat == expected && info is { Width: > 0 and <= 8192, Height: > 0 and <= 8192 } &&
                (long)info.Width * info.Height <= 32_000_000;
        }
        catch { return false; }
    }
}

public sealed class ThumbnailStore(IConfiguration config)
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private string Root => config["Assets:ThumbnailDirectory"] ?? "";

    public async Task<string?> Save(Guid id, byte[] bytes, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(Root)) return null;
        // The caller validated dimensions before decoding. The output is bounded.
        using var bitmap = SKBitmap.Decode(bytes);
        if (bitmap is null) return null;
        var ratio = Math.Min(256d / bitmap.Width, 256d / bitmap.Height);
        var width = Math.Max(1, (int)Math.Round(bitmap.Width * Math.Min(1, ratio)));
        var height = Math.Max(1, (int)Math.Round(bitmap.Height * Math.Min(1, ratio)));
        using var surface = SKSurface.Create(new SKImageInfo(width, height));
        if (surface is null) return null;
        surface.Canvas.DrawBitmap(bitmap, new SKRect(0, 0, width, height));
        using var image = surface.Snapshot();
        using var encoded = image.Encode(SKEncodedImageFormat.Webp, 70);
        if (encoded is null) return null;
        var thumbnail = encoded.ToArray();
        var max = config.GetValue<long>("Assets:MaxThumbnailBytes", 262144);
        if (thumbnail.LongLength > max || thumbnail.Length == 0) return null;
        await gate.WaitAsync(ct);
        try
        {
            Directory.CreateDirectory(Root);
            var budget = config.GetValue<long>("Assets:MaxThumbnailStorageBytes", 1073741824);
            long used = 0;
            foreach (var file in Directory.EnumerateFiles(Root, "*.webp"))
            {
                used += new FileInfo(file).Length;
                if (used + thumbnail.LongLength > budget) return null;
            }
            var target = Path.Combine(Root, id.ToString("N") + ".webp");
            var temp = Path.Combine(Root, Guid.NewGuid().ToString("N") + ".tmp");
            try
            {
                await File.WriteAllBytesAsync(temp, thumbnail, ct);
                File.Move(temp, target, overwrite: true);
                return id.ToString("N");
            }
            finally { if (File.Exists(temp)) File.Delete(temp); }
        }
        finally { gate.Release(); }
    }

    public async Task<byte[]?> Load(string locator, CancellationToken ct)
    {
        if (!Guid.TryParseExact(locator, "N", out _) || string.IsNullOrWhiteSpace(Root)) return null;
        var path = Path.Combine(Root, locator + ".webp");
        if (!File.Exists(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return null;
        var max = config.GetValue<long>("Assets:MaxThumbnailBytes", 262144);
        if (new FileInfo(path).Length is <= 0 || new FileInfo(path).Length > max) return null;
        return await File.ReadAllBytesAsync(path, ct);
    }
}
