"""
Root-level entrypoint for Streamlit Real Estate Analytics Dashboard.
Main business logic and visualizations are maintained in `src.dashboard.app`.
"""
import os
import sys

# 프로젝트 루트 경로를 sys.path에 추가
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.dashboard.app import main

if __name__ == "__main__" or "__streamlitmagic__" in globals():
    main()
