"""
Root-level compatibility wrapper for RealEstateDB.
Main implementation moved to src.db.db_manager.
"""
from src.db.db_manager import RealEstateDB

__all__ = ["RealEstateDB"]
