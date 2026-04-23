"""Video-related repository accessors."""

from app.repositories import _impl as legacy

insert_video_if_absent = legacy.insert_video_if_absent
list_videos = legacy.list_videos
count_videos = legacy.count_videos
normalize_pipeline_status_filter = legacy.normalize_pipeline_status_filter
VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES = legacy.VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES
get_video = legacy.get_video
list_videos_by_ids = legacy.list_videos_by_ids
mark_video_viewed = legacy.mark_video_viewed
get_video_detail = legacy.get_video_detail
get_transcript = legacy.get_transcript
get_article = legacy.get_article
search_documents = legacy.search_documents
mark_video_retry = legacy.mark_video_retry
requeue_done_video_for_manual_article_retry = legacy.requeue_done_video_for_manual_article_retry
update_video_thumbnail = legacy.update_video_thumbnail

delete_videos_by_ids = legacy.delete_videos_by_ids
