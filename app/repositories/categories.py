"""Category-related repository accessors."""

import app.repositories._categories as repository

parse_category_processing_stage = repository.parse_category_processing_stage

get_default_category_id = repository.get_default_category_id
list_categories = repository.list_categories
create_category = repository.create_category
rename_category = repository.rename_category
delete_category = repository.delete_category
update_category_processing_stage = repository.update_category_processing_stage
cycle_category_processing_stage = repository.cycle_category_processing_stage
reorder_categories = repository.reorder_categories
move_channels_to_category = repository.move_channels_to_category
