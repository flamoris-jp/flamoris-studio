namespace Flamoris.Studio.Server.Generation;

public sealed record LoraInput(string Name, double StrengthModel = 1, double StrengthClip = 1);
public sealed record ImageRequest(
    string PositivePrompt, string NegativePrompt, int Width, int Height,
    int Steps, double Cfg, ulong Seed, string Checkpoint, LoraInput[]? Loras);

public static class ImageValidation
{
    public static bool Valid(ImageRequest request) =>
        !string.IsNullOrWhiteSpace(request.PositivePrompt) && request.PositivePrompt.Length <= 20000 &&
        request.NegativePrompt is { Length: <= 20000 } &&
        request.Width is >= 64 and <= 4096 && request.Width % 8 == 0 &&
        request.Height is >= 64 and <= 4096 && request.Height % 8 == 0 &&
        request.Steps is >= 1 and <= 150 && double.IsFinite(request.Cfg) && request.Cfg is >= 0 and <= 100 &&
        !string.IsNullOrWhiteSpace(request.Checkpoint) && request.Checkpoint.Length <= 1024 &&
        (request.Loras is null || request.Loras.Length <= 16) &&
        (request.Loras ?? []).All(x => !string.IsNullOrWhiteSpace(x.Name) && x.Name.Length <= 1024 &&
            double.IsFinite(x.StrengthClip) && double.IsFinite(x.StrengthModel) &&
            x.StrengthClip is >= -20 and <= 20 && x.StrengthModel is >= -20 and <= 20);

    public static object Parameters(ImageRequest request) => new
    {
        checkpoint = request.Checkpoint, positive_prompt = request.PositivePrompt,
        negative_prompt = request.NegativePrompt, width = request.Width, height = request.Height,
        steps = request.Steps, cfg = request.Cfg, seed = request.Seed,
        loras = (request.Loras ?? []).Select(x => new
        {
            name = x.Name, strength_model = x.StrengthModel, strength_clip = x.StrengthClip
        }).ToArray()
    };
}
