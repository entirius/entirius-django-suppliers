from django_suppliers.tasks.feed_execution import execute_feed_task
from django_suppliers.tasks.image_download import download_supplier_images_task
from django_suppliers.tasks.preferred_strategy import evaluate_preferred_suppliers_task
from django_suppliers.tasks.push_pipeline import push_approved_for_supplier_task
from django_suppliers.tasks.retention import prune_supplier_events_task
from django_suppliers.tasks.scraper_callback import process_scraper_results_task

__all__ = [
    "download_supplier_images_task",
    "evaluate_preferred_suppliers_task",
    "execute_feed_task",
    "process_scraper_results_task",
    "prune_supplier_events_task",
    "push_approved_for_supplier_task",
]
