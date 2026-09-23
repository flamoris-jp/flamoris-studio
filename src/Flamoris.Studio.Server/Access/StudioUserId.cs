using System.Security.Claims;

namespace Flamoris.Studio.Server.Access;

public readonly record struct StudioUserId(string Value)
{
    public static StudioUserId From(ClaimsPrincipal principal) =>
        new(principal.FindFirstValue(ClaimTypes.NameIdentifier)
            ?? throw new UnauthorizedAccessException("Authentication required."));
}
