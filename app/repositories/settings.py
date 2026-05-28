"""Settings and worker-policy repository accessors."""

from app.repositories import _settings as repository

WORKER_SETTING_DEFAULTS = repository.WORKER_SETTING_DEFAULTS
RSS_FEED_MODE_DEFAULT = repository.RSS_FEED_MODE_DEFAULT

get_setting = repository.get_setting
set_setting = repository.set_setting

get_worker_settings = repository.get_worker_settings
set_worker_settings = repository.set_worker_settings
is_worker_enabled = repository.is_worker_enabled

get_policy_settings = repository.get_policy_settings
set_policy_settings = repository.set_policy_settings

get_videos_per_page_setting = repository.get_videos_per_page_setting
set_videos_per_page_setting = repository.set_videos_per_page_setting

get_telegram_settings = repository.get_telegram_settings
set_telegram_settings = repository.set_telegram_settings

get_llm_settings = repository.get_llm_settings
set_llm_settings = repository.set_llm_settings
