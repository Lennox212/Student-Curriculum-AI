from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from django.utils import timezone
import json

# Import Firebase service for AI context only
try:
    from firebase_service import firebase_service
except ImportError:
    firebase_service = None


class CustomUser(AbstractUser):
    """
    Custom user model extending AbstractUser.

    Notes:
    - completed_courses_json and planned_courses_json are JSONField-backed and store
    Python-native structures (lists). The getter/setter helpers below tolerate either
    Python objects or JSON-encoded strings to be robust against older data.
    """
    email = models.EmailField(unique=True)

    selected_concentration = models.CharField(max_length=50, blank=True, null=True)

    has_completed_setup = models.BooleanField(default=False)

    # Store completed courses as JSON (list)
    completed_courses_json = models.JSONField(default=list, encoder=DjangoJSONEncoder)

    # Store planned courses as JSON (list)
    planned_courses_json = models.JSONField(default=list, encoder=DjangoJSONEncoder)
    
    # Dashboard card style preference
    dashboard_card_style = models.CharField(max_length=20, default='gradient', blank=True)

    def __str__(self):
        return self.get_full_name() or self.username

    # ---- Completed courses helpers ----
    def get_completed_courses(self):
        """
        Return completed courses as a Python list.
        Handles cases where the field might be a JSON-encoded string or already a Python list.
        """
        val = self.completed_courses_json
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return []
        if val is None:
            return []
        return val

    def set_completed_courses(self, courses_list):
        """
        Set completed courses. Accepts a Python list (preferred) or serializable object.
        """
        # Defensive: ensure we store a list (or serializable structure)
        self.completed_courses_json = courses_list if courses_list is not None else []
        self.save(update_fields=["completed_courses_json"])

    # ---- Planned courses helpers ----
    def get_planned_courses(self):
        """
        Return planned courses as a Python list.
        Handles both Python list and JSON-encoded string for backward compatibility.
        """
        val = self.planned_courses_json
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return []
        if val is None:
            return []
        return val

    def set_planned_courses(self, courses_list):
        """
        Set planned courses. Accepts a Python list (preferred) or serializable object.
        """
        self.planned_courses_json = courses_list if courses_list is not None else []
        self.save(update_fields=["planned_courses_json"])

    # ===== MINIMAL AI INTEGRATION - get context from Firebase =====
    def get_ai_context(self):
        """
        Get context from Firebase for AI. This is read-only and doesn't modify anything.
        """
        if firebase_service:
            return firebase_service.get_user_data_for_ai(str(self.id or self.pk))
        return {}

    # ===== Properties to prevent "no such column" errors =====
    @property
    def ai_preferences(self):
        """Return empty dict to prevent column errors"""
        return {}

    @property
    def allow_ai_memory(self):
        """Return True by default to prevent column errors"""
        return True

    @property
    def ai_context_limit(self):
        """Return default limit to prevent column errors"""
        return 10



# ===== FIREBASE PROXY MODELS FOR CURRICULUM MANAGEMENT =====
# Django interface with Firebase

class Curriculum(models.Model):
    """
    Proxy model for curriculum data stored in Firebase.
    Represents a curriculum document from Firestore's 'curricula' collection.
    """
    code = models.CharField(max_length=50, primary_key=True)

    # Fields stored in Firebase
    concentration = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    graduation_requirements = models.JSONField(default=dict, blank=True)
    validation_rules = models.JSONField(default=dict, blank=True)
    ai_recommendation_settings = models.JSONField(default=dict, blank=True)
    last_updated = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False  # Don't create database table
        verbose_name = "Curriculum"
        verbose_name_plural = "Curricula"

    def __str__(self):
        concentration_title = self.concentration.get('title', 'Unknown') if isinstance(self.concentration,
                                                                                     dict) else 'Unknown'
        return f"{self.code} - {concentration_title}"


class Term(models.Model):
    """
    Proxy model for term data stored in Firebase.
    Represents a term document from Firestore's 'curricula/{code}/terms' collection.
    Read-only in admin - used for navigation to courses.
    """
    # Composite primary key: curriculum_code:term_id
    id = models.CharField(max_length=30, primary_key=True)
    curriculum_code = models.CharField(max_length=30)
    term_id = models.CharField(max_length=30)

    # Individual term_info fields
    term_name = models.CharField(max_length=100, blank=True)
    description = models.TextField(max_length=500,blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    trimester = models.CharField(max_length=30, blank=True)
    recommended_trimester = models.JSONField(default=list, blank=True)
    typical_next_terms = models.JSONField(default=list, blank=True)
    critical_courses = models.JSONField(default=list, blank=True)
    min_credits = models.PositiveIntegerField(null=True, blank=True)
    max_credits = models.PositiveIntegerField(null=True, blank=True)
    total_credits_in_term = models.PositiveIntegerField(null=True, blank=True)

    # Keep full objects for reference
    term_info = models.JSONField(default=dict, blank=True)
    concentration = models.JSONField(default=dict, blank=True)

    class Meta:
        managed = False  # Don't create database table
        verbose_name = "Term"
        verbose_name_plural = "Terms"

    def __str__(self):
        return f"{self.curriculum_code} - {self.term_name or self.term_id}"


class Course(models.Model):
    """
    Proxy model for course data stored in Firebase.
    Represents a course document from Firestore's 'curricula/{code}/terms/{term}/courses' collection.
    """
    # Composite primary key: curriculum_code:term_id:course_code
    id = models.CharField(max_length=150, primary_key=True)
    curriculum_code = models.CharField(max_length=50)
    term_id = models.CharField(max_length=50)

    # Course fields from Firebase
    code = models.CharField(max_length=30)
    title = models.CharField(max_length=100)
    credits = models.PositiveIntegerField(default=0)
    design_credits = models.PositiveIntegerField(default=0)
    component = models.CharField(max_length=150, blank=True)
    description = models.TextField(max_length=500, blank=True)
    prerequisites = models.CharField(max_length=500, blank=True)
    corequisites = models.CharField(max_length=500, blank=True)
    term = models.CharField(max_length=50, blank=True)
    OL = models.BooleanField(default=False, verbose_name="Online Available")
    OL_only = models.BooleanField(default=False, verbose_name="Online Only")

    class Meta:
        managed = False  # Don't create database table
        verbose_name = "Course"
        verbose_name_plural = "Courses"

    def __str__(self):
        return f"{self.code} - {self.title}"