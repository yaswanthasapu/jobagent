import pytest
from unittest.mock import AsyncMock, MagicMock
from automation.pages.job_search_page import JobSearchPage

@pytest.mark.asyncio
async def test_job_search_page_natural_keywords_without_quotes():
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()

    search_page = JobSearchPage(mock_page)
    search_page.human_delay = AsyncMock()

    await search_page.navigate_to_search(
        keyword="QA Engineer",
        location="Hyderabad",
        easy_apply_only=True,
        date_posted="24h"
    )

    assert mock_page.goto.called
    called_url = mock_page.goto.call_args[0][0]

    # Verify NO quotes (%22) are added around keywords
    assert "keywords=QA+Engineer" in called_url
    assert "%22" not in called_url
    assert "location=Hyderabad" in called_url
    assert "f_AL=true" in called_url
    assert "f_TPR=r86400" in called_url

@pytest.mark.asyncio
async def test_job_search_page_pagination_and_relevance_sort():
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_timeout = AsyncMock()

    search_page = JobSearchPage(mock_page)
    search_page.human_delay = AsyncMock()

    await search_page.navigate_to_search(
        keyword="QA Engineer",
        location="Hyderabad",
        easy_apply_only=True,
        date_posted="any",
        sort_by="date",
        start=25
    )

    assert mock_page.goto.called
    called_url = mock_page.goto.call_args[0][0]

    assert "keywords=QA+Engineer" in called_url
    assert "location=Hyderabad" in called_url
    assert "start=25" in called_url
    # Date posted 'any' defaults sort to Relevance (sortBy=R)
    assert "sortBy=R" in called_url

@pytest.mark.asyncio
async def test_job_search_page_click_next_page():
    mock_page = MagicMock()
    search_page = JobSearchPage(mock_page)
    search_page.human_delay = AsyncMock()
    search_page.scroll_job_list_to_bottom = AsyncMock()

    mock_btn = MagicMock()
    mock_btn.count = AsyncMock(return_value=1)
    mock_btn.is_visible = AsyncMock(return_value=True)
    mock_btn.get_attribute = AsyncMock(return_value=None)
    mock_btn.scroll_into_view_if_needed = AsyncMock()
    mock_btn.click = AsyncMock()

    mock_page.locator.return_value.first = mock_btn

    clicked = await search_page.click_next_page()
    assert clicked is True
    assert mock_btn.click.called


