"""Presentation grouping only; every query still scopes both catalog owners."""

from sqlalchemy import case, exists, or_, select
from sqlalchemy.orm import aliased

from .db import Asset, Execution


def category_expression():
    related = aliased(Asset)
    own_siblings = (related.execution_id == Asset.execution_id,
                    related.user_id == Asset.user_id, related.availability != "deleted")
    wav = exists(select(related.id).where(*own_siblings,
                 related.media_kind == "audio", related.mime_type == "audio/wav").correlate(Asset))
    score = exists(select(related.id).where(*own_siblings,
                   related.mime_type.in_(("text/vnd.abc", "audio/midi"))).correlate(Asset))
    music = or_(Execution.category == "music", Execution.operation.in_(("music.generate", "music.transcribe")),
                # Older external imports retained media kinds rather than the
                # capability category. Group their WAV/score metadata safely.
                (Execution.operation == "external.import") & score)
    return case(
        (music & wav, "music"),
        ((Execution.category == "speech") & (Asset.media_kind == "audio"), "speech"),
        (Asset.media_kind == "image", "image"),
        (Asset.media_kind == "video", "video"),
        ((Asset.media_kind == "audio") & (Asset.mime_type == "audio/wav"), "music"),
        else_="other",
    )
