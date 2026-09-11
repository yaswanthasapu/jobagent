import pytest
from unittest.mock import AsyncMock, MagicMock
from automation.pages.easy_apply_modal import EasyApplyModal
from models.form import FormField, FormFieldType

@pytest.fixture
def mock_page():
    page = MagicMock()
    return page

@pytest.mark.asyncio
async def test_get_validation_errors(mock_page):
    modal_page = EasyApplyModal(mock_page)
    
    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal
    
    # Mock error locators
    error_loc = MagicMock()
    error_loc.count = AsyncMock(return_value=2)
    
    err1 = MagicMock()
    err1.inner_text = AsyncMock(return_value="Please make a selection")
    err2 = MagicMock()
    err2.inner_text = AsyncMock(return_value="Enter a whole number between 0 and 99")
    
    items = [err1, err2]
    error_loc.nth = MagicMock(side_effect=lambda idx: items[idx])
    mock_modal.locator.return_value = error_loc
    
    errors = await modal_page.get_validation_errors()
    assert len(errors) == 2
    assert "Please make a selection" in errors
    assert "Enter a whole number between 0 and 99" in errors
    assert await modal_page.has_validation_errors() is True

@pytest.mark.asyncio
async def test_no_validation_errors(mock_page):
    modal_page = EasyApplyModal(mock_page)
    
    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal
    
    error_loc = MagicMock()
    error_loc.count = AsyncMock(return_value=0)
    mock_modal.locator.return_value = error_loc
    
    errors = await modal_page.get_validation_errors()
    assert len(errors) == 0
    assert await modal_page.has_validation_errors() is False

@pytest.mark.asyncio
async def test_is_review_step_blocked_by_errors(mock_page):
    modal_page = EasyApplyModal(mock_page)
    modal_page.has_validation_errors = AsyncMock(return_value=True)
    
    assert await modal_page.is_review_step() is False

@pytest.mark.asyncio
async def test_is_review_step_submit_visible(mock_page):
    modal_page = EasyApplyModal(mock_page)
    modal_page.has_validation_errors = AsyncMock(return_value=False)
    
    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal
    
    submit_btn = MagicMock()
    submit_btn.is_visible = AsyncMock(return_value=True)
    mock_modal.locator.return_value.first = submit_btn
    
    assert await modal_page.is_review_step() is True

@pytest.mark.asyncio
async def test_click_review_does_not_click_next(mock_page):
    modal_page = EasyApplyModal(mock_page)
    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal
    
    btn = MagicMock()
    btn.is_visible = AsyncMock(return_value=True)
    btn.inner_text = AsyncMock(return_value="Next")
    btn.click = AsyncMock()
    
    mock_modal.locator.return_value.first = btn
    
    # Should not click 'Next' button
    result = await modal_page.click_review()
    assert result is False
    btn.click.assert_not_called()

@pytest.mark.asyncio
async def test_click_next_does_not_click_review(mock_page):
    modal_page = EasyApplyModal(mock_page)
    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal
    
    btn = MagicMock()
    btn.is_visible = AsyncMock(return_value=True)
    btn.inner_text = AsyncMock(return_value="Review your application")
    btn.click = AsyncMock()
    
    mock_modal.locator.return_value.first = btn
    
    # Should not click 'Review' button
    result = await modal_page.click_next()
    assert result is False
    btn.click.assert_not_called()

@pytest.mark.asyncio
async def test_scroll_modal_content(mock_page):
    modal_page = EasyApplyModal(mock_page)
    modal_page.human_delay = AsyncMock()

    mock_modal = MagicMock()
    mock_page.locator.return_value.first = mock_modal

    mock_container = MagicMock()
    mock_container.count = AsyncMock(return_value=1)
    mock_container.evaluate = AsyncMock(return_value=True)

    mock_modal.locator.return_value.first = mock_container

    scrolled = await modal_page.scroll_modal_content(step=450)
    assert scrolled is True
    assert mock_container.evaluate.called

