"""YouTube Data API video-category enums (Issue 83).

These IDs come from the YouTube Data API v3 ``videoCategories.list`` endpoint
(US region) and are stable as of 2026. We use the ID-as-string form so the
JSONB array in ``creator_identity.niches`` stays a plain list and round-trips
cleanly through JSON. The labels here match what creators see in YouTube
Studio so the intake form feels familiar.

We deliberately filter out region-specific / deprecated categories (18, 21,
30+, etc.) to keep the list under ~15 options — the 2026 onboarding research
warns that >15 enum choices materially hurts completion rates.

If you ever need the live list (creator-locale customization, etc.), call
``GET https://www.googleapis.com/youtube/v3/videoCategories?part=snippet&regionCode=<cc>``
with an OAuth token. For our intake we trust this static snapshot.
"""

from typing import TypedDict


class CategoryOption(TypedDict):
    id: str
    label: str


# Ordered roughly by creator-tool relevance (Education/Howto/Gaming first;
# Nonprofits last). Each is one ``id`` + one ``label`` — matches the multi-select
# UI shape directly.
NICHE_OPTIONS: list[CategoryOption] = [
    {"id": "27", "label": "Education"},
    {"id": "26", "label": "How-to & Style"},
    {"id": "28", "label": "Science & Technology"},
    {"id": "20", "label": "Gaming"},
    {"id": "22", "label": "People & Blogs"},
    {"id": "24", "label": "Entertainment"},
    {"id": "23", "label": "Comedy"},
    {"id": "10", "label": "Music"},
    {"id": "17", "label": "Sports"},
    {"id": "25", "label": "News & Politics"},
    {"id": "19", "label": "Travel & Events"},
    {"id": "1", "label": "Film & Animation"},
    {"id": "15", "label": "Pets & Animals"},
    {"id": "2", "label": "Autos & Vehicles"},
    {"id": "29", "label": "Nonprofits & Activism"},
]

# Lookup set for fast validation on POST.
NICHE_IDS: frozenset[str] = frozenset(opt["id"] for opt in NICHE_OPTIONS)


def label_for(niche_id: str) -> str | None:
    """Return the human label for a niche id, or None if unknown."""
    for opt in NICHE_OPTIONS:
        if opt["id"] == niche_id:
            return opt["label"]
    return None


def labels_for(niche_ids: list[str]) -> list[str]:
    """Map a list of niche ids to labels, dropping any unknown ids."""
    return [label for nid in niche_ids if (label := label_for(nid)) is not None]
