import allure
import pytest
from playwright.sync_api import Page
from pytest_check import check
from playwright_tests.core.utilities import Utilities
from playwright_tests.messages.my_profile_pages_messages.edit_settings_page_messages import (
    EditSettingsPageMessages,
)
from playwright_tests.pages.sumo_pages import SumoPages


# T5697867, T5697927
@pytest.mark.userSettings
def test_all_checkboxes_can_be_selected_and_saved(page: Page, create_user_factory):
    utilities = Utilities(page)
    sumo_pages = SumoPages(page)
    test_user = create_user_factory()

    with allure.step(f"Signing in with {test_user['username']} user account"):
        utilities.start_existing_session(cookies=test_user)

    with check, allure.step("Checking all user settings and verifying that the correct"
                            " notification banner is displayed and all checkboxes are checked"):
        sumo_pages.edit_profile_flow.check_all_user_settings()
        assert (sumo_pages.edit_my_profile_settings_page.
                settings_saved_notif_banner_txt() == EditSettingsPageMessages.
                MODIFIED_SETTINGS_NOTIFICATION_BANNER_MESSAGE)
        assert sumo_pages.edit_my_profile_settings_page.are_all_checkbox_checked()

    with allure.step("Unchecking all the checkboxes and verifying that the correct notification"
                     " banner is displayed and all checkboxes are unchecked"):
        sumo_pages.edit_profile_flow.check_all_user_settings()
        assert (sumo_pages.edit_my_profile_settings_page.
                settings_saved_notif_banner_txt() == EditSettingsPageMessages.
                MODIFIED_SETTINGS_NOTIFICATION_BANNER_MESSAGE)
        assert not sumo_pages.edit_my_profile_settings_page.are_all_checkbox_checked()
