using SixLabors.ImageSharp;
using SixLabors.ImageSharp.Formats.Webp;
using SixLabors.ImageSharp.Processing;

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
        if (mime == "image/png") return bytes.AsSpan().StartsWith([137, 80, 78, 71, 13, 10, 26, 10]);
        if (mime == "image/jpeg") return bytes.AsSpan().StartsWith([255, 216, 255]);
        if (mime == "image/webp") return bytes.Length >= 12 &&
            bytes.AsSpan().StartsWith("RIFF"u8) && bytes.AsSpan(8, 4).SequenceEqual("WEBP"u8);
        return false;
    }
}

public sealed class ThumbnailStore(IConfiguration config)
{
    private string Root => config["Assets:ThumbnailDirectory"] ?? "";

    public async Task<string?> Save(Guid id, byte[] bytes, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(Root)) return null;
        // Bounded decode: reject huge pixel dimensions before allocating the full image.
        var info = Image.Identify(bytes);
        if (info is null || info.Width <= 0 || info.Height <= 0 ||
            info.Width > 8192 || info.Height > 8192 || (long)info.Width * info.Height > 32_000_000)
            return null;
        using var image = Image.Load(bytes);
        image.Mutate(x => x.Resize(new ResizeOptions { Size = new Size(256, 256), Mode = ResizeMode.Max }));
        await using var stream = new MemoryStream();
        await image.SaveAsync(stream, new WebpEncoder { Quality = 70 }, ct);
        var max = config.GetValue<long>("Assets:MaxThumbnailBytes", 262144);
        if (stream.Length > max || stream.Length <= 0) return null;
        Directory.CreateDirectory(Root);
        var target = Path.Combine(Root, id.ToString("N") + ".webp");
        var temp = Path.Combine(Root, Guid.NewGuid().ToString("N") + ".tmp");
        try
        {
            await File.WriteAllBytesAsync(temp, stream.ToArray(), ct);
            File.Move(temp, target, overwrite: true);
            return id.ToString("N");
        }
        finally { if (File.Exists(temp)) File.Delete(temp); }
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
