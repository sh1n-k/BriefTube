"""Settings and worker-policy repository accessors."""

from app import repository as legacy

WORKER_SETTING_DEFAULTS = legacy.WORKER_SETTING_DEFAULTS
RSS_FEED_MODE_DEFAULT = legacy.RSS_FEED_MODE_DEFAULT

get_setting = legacy.get_setting
set_setting = legacy.set_setting

get_worker_settings = legacy.get_worker_settings
set_worker_settings = legacy.set_worker_settings
is_worker_enabled = legacy.is_worker_enabled

get_policy_settings = legacy.get_policy_settings
set_policy_settings = legacy.set_policy_settings

get_videos_per_page_setting = legacy.get_videos_per_page_setting
set_videos_per_page_setting = legacy.set_videos_per_page_setting

get_llm_settings = legacy.get_llm_settings
set_llm_settings = legacy.set_llm_settings
