from app.ai.schemas import TOOL_DECLARATIONS


def test_tool_names_unique():
    names = [x["name"] for x in TOOL_DECLARATIONS]
    assert len(names) == len(set(names))
    assert "web_search" in names
    assert "recent_chats" in names
    assert "set_chat_wallpaper" in names
