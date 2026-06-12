"""Video-related repository accessors."""

from app.repositories import _videos as repository

insert_video_if_absent = repository.insert_video_if_absent
insert_videos_if_absent_batch = repository.insert_videos_if_absent_batch
list_videos = repository.list_videos
count_videos = repository.count_videos
normalize_pipeline_status_filter = repository.normalize_pipeline_status_filter
VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES = repository.VIDEO_LIST_FILTER_CORE_PIPELINE_STATUSES
get_video = repository.get_video
list_videos_by_ids = repository.list_videos_by_ids
mark_video_viewed = repository.mark_video_viewed
get_video_detail = repository.get_video_detail
get_transcript = repository.get_transcript
get_article = repository.get_article
search_documents = repository.search_documents
mark_video_retry = repository.mark_video_retry
requeue_done_video_for_manual_article_retry = repository.requeue_done_video_for_manual_article_retry
update_video_thumbnail = repository.update_video_thumbnail

delete_videos_by_ids = repository.delete_videos_by_ids
