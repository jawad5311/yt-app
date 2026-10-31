"""Regression tests for Issue 15:
Require unambiguous channel identity matches.
"""


from src.automation.watching_worker import match_channel, normalize_channel_text
from src.models.channel import Channel, parse_channel_identity


def test_parse_channel_identity():
    """Verify supported channel URLs parse into explicit identity forms."""
    # Handles
    assert parse_channel_identity("https://www.youtube.com/@mkbhd") == ("handle", "@mkbhd")
    assert parse_channel_identity("https://www.youtube.com/@mkbhd/videos") == ("handle", "@mkbhd")
    assert parse_channel_identity("/@mkbhd") == ("handle", "@mkbhd")
    assert parse_channel_identity("@mkbhd") == ("handle", "@mkbhd")
    assert parse_channel_identity("https://m.youtube.com/@CaseFold/featured") == ("handle", "@casefold")

    # Channel IDs (preserves case)
    assert parse_channel_identity("https://www.youtube.com/channel/UCbjLHv77aXk") == ("id", "UCbjLHv77aXk")
    assert parse_channel_identity("youtube.com/channel/UC12345/videos") == ("id", "UC12345")
    assert parse_channel_identity("/channel/UCtestCasePreserved") == ("id", "UCtestCasePreserved")

    # Custom and user URLs
    assert parse_channel_identity("https://www.youtube.com/c/MyCustomChannel/videos") == ("custom", "mycustomchannel")
    assert parse_channel_identity("https://www.youtube.com/user/OldLegacyUser") == ("user", "oldlegacyuser")

    # Empty / unrelated
    assert parse_channel_identity("") == (None, None)
    assert parse_channel_identity("https://www.youtube.com/results?search_query=test") == (None, None)


def test_equivalent_supported_url_forms_match():
    """1. Equivalent supported URL forms match."""
    # Stored channel has full URL with /videos, scanned has relative /@handle
    channels = [Channel(name="Marques Brownlee", url="https://www.youtube.com/@mkbhd/videos")]
    match = match_channel(scanned_name="MKBHD", scanned_url="/@mkbhd", channels=channels)
    assert match is not None
    assert match.url == "https://www.youtube.com/@mkbhd/videos"

    # Stored channel has channel ID, scanned has mobile channel ID with /shorts
    channels_id = [Channel(name="Creator", url="https://www.youtube.com/channel/UCbjLHv77aXk")]
    match_id = match_channel(scanned_name="Creator", scanned_url="https://m.youtube.com/channel/UCbjLHv77aXk/shorts", channels=channels_id)
    assert match_id is not None
    assert match_id.name == "Creator"


def test_handle_with_extra_suffix_does_not_match_shorter_handle():
    """2. A handle with an extra suffix does not match a shorter handle."""
    channels = [Channel(name="Main Channel", url="https://www.youtube.com/@handle")]
    # Scanned card is from a different handle that starts with the same substring
    match = match_channel(scanned_name="Main Channel", scanned_url="https://www.youtube.com/@handle-suffix", channels=channels)
    assert match is None

    # Reverse: stored has suffix, scanned is base
    channels_suffix = [Channel(name="Suffix Channel", url="https://www.youtube.com/@handle-suffix")]
    match_rev = match_channel(scanned_name="Suffix Channel", scanned_url="https://www.youtube.com/@handle", channels=channels_suffix)
    assert match_rev is None


def test_different_channel_identities_with_identical_display_names_do_not_match():
    """3. Different channel identities with identical display names do not match."""
    # Two channels share the exact same display name 'Science', but have different handles
    channels = [Channel(name="Science", url="https://www.youtube.com/@science_one")]
    match = match_channel(scanned_name="Science", scanned_url="https://www.youtube.com/@science_two", channels=channels)
    assert match is None


def test_similar_names_do_not_match_through_fuzzy_similarity():
    """4. Similar names do not match through fuzzy similarity."""
    channels = [Channel(name="MR Whose The Bose", url="")]
    # YouTube has 'Mrwhosetheboss'
    match = match_channel(scanned_name="Mrwhosetheboss", scanned_url="", channels=channels)
    assert match is None

    channels2 = [Channel(name="Linus Tech Tips", url="")]
    match2 = match_channel(scanned_name="Linus Tech Tipz", scanned_url="", channels=channels2)
    assert match2 is None


def test_handle_and_channel_id_url_without_verified_equivalence_remain_unmatched():
    """5. A handle and channel-ID URL without verified equivalence remain unmatched."""
    # Stored channel uses handle URL, scanned video uses /channel/UC... URL
    # Even if display name matches, without a verified mapping they must not be conflated
    channels = [Channel(name="Gaming Channel", url="https://www.youtube.com/@gamingchannel")]
    match = match_channel(
        scanned_name="Gaming Channel",
        scanned_url="https://www.youtube.com/channel/UCgaming123456789012",
        channels=channels,
    )
    assert match is None


def test_ambiguous_name_only_candidates_return_no_match():
    """6. Ambiguous name-only candidates return no match."""
    # Two distinct stored channels share the same display name
    channels = [
        Channel(name="Daily Vlogs", url=""),
        Channel(name="Daily Vlogs", url=""),
    ]
    # Scanned video provides only the name, without URL
    match = match_channel(scanned_name="Daily Vlogs", scanned_url="", channels=channels)
    assert match is None


def test_non_latin_names_not_reduced_to_empty_or_unrelated_matches():
    """7. Non-Latin names are not reduced to empty identities or unrelated matches."""
    # Verify normalization does not collapse non-Latin text to empty
    arabic_1 = "قناة الجزيرة"
    arabic_2 = "قناة العربية"
    norm_1 = normalize_channel_text(arabic_1)
    norm_2 = normalize_channel_text(arabic_2)
    assert norm_1 != ""
    assert norm_2 != ""
    assert norm_1 != norm_2

    # Different non-Latin names must NOT match
    channels = [Channel(name=arabic_1, url="")]
    assert match_channel(scanned_name=arabic_2, scanned_url="", channels=channels) is None

    # Identical non-Latin names MUST match unambiguously
    match = match_channel(scanned_name=arabic_1, scanned_url="", channels=channels)
    assert match is not None
    assert match.name == arabic_1

    # Russian / Cyrillic
    cyrillic = "Русский Канал"
    ch_cyr = [Channel(name=cyrillic, url="")]
    assert match_channel(scanned_name=cyrillic, scanned_url="", channels=ch_cyr) is not None
    assert match_channel(scanned_name="Другой Канал", scanned_url="", channels=ch_cyr) is None


def test_disabled_and_archived_channels_remain_ineligible():
    """8. Disabled and archived channels remain ineligible."""
    channels = [
        Channel(name="Active Channel", url="https://www.youtube.com/@active", enabled=True, archived=False),
        Channel(name="Disabled Channel", url="https://www.youtube.com/@disabled", enabled=False, archived=False),
        Channel(name="Archived Channel", url="https://www.youtube.com/@archived", enabled=True, archived=True),
    ]

    # Exact handle match on disabled channel -> None
    assert match_channel("Disabled Channel", "https://www.youtube.com/@disabled", channels) is None
    # Exact handle match on archived channel -> None
    assert match_channel("Archived Channel", "https://www.youtube.com/@archived", channels) is None
    # Exact handle match on active channel -> Matches
    assert match_channel("Active Channel", "https://www.youtube.com/@active", channels) is not None


def test_existing_legitimate_exact_identity_matches_continue_working():
    """9. Existing legitimate exact-identity matches continue working."""
    channels = [
        Channel(name="Veritasium", url="https://www.youtube.com/@veritasium"),
        Channel(name="Numberphile", url="https://www.youtube.com/@numberphile"),
    ]

    # Matching Veritasium by exact handle with different casing
    m1 = match_channel("Veritasium", "https://www.youtube.com/@Veritasium", channels)
    assert m1 is not None
    assert m1.name == "Veritasium"

    # Matching Numberphile by relative handle
    m2 = match_channel("Numberphile", "/@numberphile/videos", channels)
    assert m2 is not None
    assert m2.name == "Numberphile"

    # Exact name-only match when unambiguous
    channels_name_only = [Channel(name="Unambiguous Creator", url="")]
    m3 = match_channel("Unambiguous Creator", "", channels_name_only)
    assert m3 is not None
    assert m3.name == "Unambiguous Creator"
