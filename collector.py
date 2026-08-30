"""
Root-level compatibility entrypoint for Real Estate Data Collector.
Main implementation moved to src.collector.collector.
"""
from src.collector.collector import (
    main,
    run_collection,
    fetch_page,
    fetch_all_pages_for_month,
    clean_and_filter,
    load_config,
    generate_year_month_list,
    get_rolling_year_month_list,
    get_kst_now,
)

if __name__ == "__main__":
    main()
