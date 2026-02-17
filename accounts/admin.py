from django.contrib import admin
from django.http import HttpResponseRedirect
from django.shortcuts import redirect
from .models import CustomUser
from .models import Curriculum, Term, Course
from firebase_service import firebase_service
from django.contrib import messages
from django.utils.html import format_html
from django import forms
from .widgets import PrettyJSONWidget
from django.urls import path, reverse
from django.contrib.auth.models import Group


# Unregister default Django models
admin.site.unregister(Group)

# ===== FIREBASE QUERYSET WRAPPER =====

class MockQuery:
    """
    Mock Query object that Django admin expects.
    Provides minimal attributes needed for admin list views.
    """

    def __init__(self, model=None):
        self.model = model
        self.select_related = False
        self.order_by = ()
        self.low_mark = 0
        self.high_mark = None
        self.default_ordering = True
        self.max_depth = None
        self.combinator = None
        self.combinator_all = False
        self.combined_queries = ()
        self.extra_select = {}
        self.annotations = {}
        self.extra_select_mask = None
        self._extra = {}
        self.deferred_loading = (frozenset(), True)
        self.group_by = None
        self.distinct = False
        self.distinct_fields = ()
        self.select_for_update = False
        self.select_for_update_nowait = False
        self.select_for_update_skip_locked = False
        self.select_for_update_of = ()
        self.select_for_no_key_update = False
        self.subquery = False

    def get_meta(self):
        """Return model's meta"""
        if self.model:
            return self.model._meta
        return None

    def __str__(self):
        return "MockQuery"

    def chain(self, klass=None):
        """Return a copy of self"""
        return MockQuery(model=self.model)

    def clone(self):
        """Return a copy of self"""
        return MockQuery(model=self.model)

    def get_count(self, using):
        return 0

    def has_filters(self):
        return False

    def get_aggregation(self, using, added_aggregate_names):
        return {}

    @property
    def is_sliced(self):
        return self.low_mark != 0 or self.high_mark is not None


class FirebaseQuerySet:
    """
    A QuerySet-like wrapper for Firebase data.
    Provides the minimal interface that Django admin expects.
    """

    def __init__(self, data=None, model=None):
        self._data = list(data) if data is not None else []
        self.model = model
        self._result_cache = None
        self.query = MockQuery(model=model)  # Add mock query object
        self._prefetch_related_lookups = ()
        self._iterable_class = list
        self._sticky_filter = False
        self._for_write = False
        self._db = None
        self._hints = {}

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __getitem__(self, k):
        """Support slicing and indexing for pagination"""
        if isinstance(k, slice):
            return FirebaseQuerySet(self._data[k], model=self.model)
        return self._data[k]

    def __bool__(self):
        return bool(self._data)

    def count(self):
        return len(self._data)

    def exists(self):
        return len(self._data) > 0

    def distinct(self):
        """Return self - Firebase data is already distinct"""
        return self

    def all(self):
        """Return a copy of self"""
        return FirebaseQuerySet(self._data, model=self.model)

    def none(self):
        """Return an empty queryset"""
        return FirebaseQuerySet([], model=self.model)

    def filter(self, **kwargs):
        """
        Basic filtering support.
        Supports simple field lookups and __icontains for search.
        """
        if not kwargs:
            return self

        filtered_data = []
        for obj in self._data:
            match = True
            for key, value in kwargs.items():
                # Handle __icontains lookup
                if '__icontains' in key:
                    field_name = key.replace('__icontains', '')
                    obj_value = getattr(obj, field_name, None)
                    if obj_value is None or str(value).lower() not in str(obj_value).lower():
                        match = False
                        break
                # Handle __exact or direct lookup
                elif '__exact' in key:
                    field_name = key.replace('__exact', '')
                    if getattr(obj, field_name, None) != value:
                        match = False
                        break
                # Handle __in lookup
                elif '__in' in key:
                    field_name = key.replace('__in', '')
                    if getattr(obj, field_name, None) not in value:
                        match = False
                        break
                # Handle pk lookup
                elif key == 'pk':
                    if getattr(obj, 'pk', None) != value and getattr(obj, 'id', None) != value:
                        match = False
                        break
                # Direct field comparison
                else:
                    if getattr(obj, key, None) != value:
                        match = False
                        break

            if match:
                filtered_data.append(obj)

        return FirebaseQuerySet(filtered_data, model=self.model)

    def exclude(self, **kwargs):
        """Basic exclude support"""
        if not kwargs:
            return self

        excluded_data = []
        for obj in self._data:
            exclude = False
            for key, value in kwargs.items():
                if '__exact' in key:
                    field_name = key.replace('__exact', '')
                    if getattr(obj, field_name, None) == value:
                        exclude = True
                        break
                elif getattr(obj, key, None) == value:
                    exclude = True
                    break

            if not exclude:
                excluded_data.append(obj)

        return FirebaseQuerySet(excluded_data, model=self.model)

    def order_by(self, *fields):
        """
        Basic ordering support.
        Supports field names and '-field' for descending.
        """
        if not fields:
            return self

        data = list(self._data)

        for field in reversed(fields):
            reverse = False
            if field.startswith('-'):
                reverse = True
                field = field[1:]

            # Handle ordering
            def get_sort_key(obj):
                val = getattr(obj, field, None)
                # Handle None values
                if val is None:
                    return (1, '')  # Put None values last
                return (0, val)

            data.sort(key=get_sort_key, reverse=reverse)

        return FirebaseQuerySet(data, model=self.model)

    def values_list(self, *fields, flat=False):
        """Basic values_list support for admin filters"""
        result = []
        for obj in self._data:
            if len(fields) == 1 and flat:
                result.append(getattr(obj, fields[0], None))
            else:
                result.append(tuple(getattr(obj, f, None) for f in fields))
        return result

    def values(self, *fields):
        """Basic values support"""
        result = []
        for obj in self._data:
            if fields:
                result.append({f: getattr(obj, f, None) for f in fields})
            else:
                result.append(obj.__dict__.copy())
        return result

    def first(self):
        """Return first item or None"""
        return self._data[0] if self._data else None

    def last(self):
        """Return last item or None"""
        return self._data[-1] if self._data else None

    def get(self, **kwargs):
        """Get a single object matching the criteria"""
        filtered = self.filter(**kwargs)
        if len(filtered) == 0:
            raise self.model.DoesNotExist("Object not found")
        if len(filtered) > 1:
            raise self.model.MultipleObjectsReturned("Multiple objects returned")
        return filtered[0]

    # These methods are needed for Django admin compatibility
    def _clone(self):
        c = FirebaseQuerySet(self._data, model=self.model)
        c._prefetch_related_lookups = self._prefetch_related_lookups
        c._sticky_filter = self._sticky_filter
        c._for_write = self._for_write
        c._db = self._db
        c._hints = self._hints.copy()
        return c

    def using(self, alias):
        """Database alias - not applicable for Firebase, return self"""
        return self

    def select_related(self, *fields):
        """No-op for Firebase"""
        return self

    def prefetch_related(self, *fields):
        """No-op for Firebase"""
        return self

    def annotate(self, **kwargs):
        """No-op for Firebase - just return self"""
        return self

    @property
    def ordered(self):
        """Required by Django admin for pagination"""
        return True

    @property
    def db(self):
        """Fake database alias"""
        return 'default'

    def _add_hints(self, **hints):
        """Add hints - no-op for Firebase"""
        self._hints.update(hints)

    def resolve_expression(self, *args, **kwargs):
        """Required for some Django internals"""
        return self

    def _filter_or_exclude(self, negate, args, kwargs):
        """Internal filter method"""
        if negate:
            return self.exclude(**kwargs)
        return self.filter(**kwargs)

    def _filter_or_exclude_inplace(self, negate, args, kwargs):
        """In-place filter - just return filtered copy"""
        return self._filter_or_exclude(negate, args, kwargs)

    def complex_filter(self, filter_obj):
        """Handle complex Q objects - simplified implementation"""
        return self

    def _combinator_query(self, combinator, *other_qs, all=False):
        """Handle union/intersection - return self"""
        return self


# ===== FIREBASE HELPER FUNCTIONS =====

def firebase_to_curriculum(curriculum_data):
    """
    Convert Firebase curriculum dict to Curriculum model instance.
    """
    curriculum = Curriculum(
        code=curriculum_data.get('code', ''),
        concentration=curriculum_data.get('concentration', {}),
        metadata=curriculum_data.get('metadata', {}),
        graduation_requirements=curriculum_data.get('graduation_requirements', {}),
        validation_rules=curriculum_data.get('validation_rules', {}),
        ai_recommendation_settings=curriculum_data.get('ai_recommendation_settings', {}),
        last_updated=curriculum_data.get('last_updated'),
    )
    return curriculum


def firebase_to_term(term_data, curriculum_code):
    """
    Convert Firebase term dict to Term model instance.
    Composite ID format: {curriculum_code}:{term_id}
    """
    term_id = term_data.get('term_id', '')
    term_info = term_data.get('term_info', {})

    term = Term(
        id=f"{curriculum_code}:{term_id}",
        curriculum_code=curriculum_code,
        term_id=term_id,
        term_name=term_info.get('term_name', ''),
        description=term_info.get('description', ''),
        year=term_info.get('year'),
        trimester=term_info.get('trimester', ''),
        recommended_trimester=term_info.get('recommended_trimester', []),
        typical_next_terms=term_info.get('typical_next_terms', []),
        critical_courses=term_info.get('critical_courses', []),
        min_credits=term_info.get('min_credits'),
        max_credits=term_info.get('max_credits'),
        total_credits_in_term=term_info.get('total_credits_in_term'),
        term_info=term_info,
        concentration=term_data.get('concentration', {}),
    )
    return term


def firebase_to_course(course_data, curriculum_code, term_id):
    """
    Convert Firebase course dict to Course model instance.
    Composite ID format: {curriculum_code}:{term_id}:{course_code}
    """
    course_code = course_data.get('code', '')

    course = Course(
        id=f"{curriculum_code}:{term_id}:{course_code}",
        curriculum_code=curriculum_code,
        term_id=term_id,
        code=course_code,
        title=course_data.get('title', ''),
        credits=course_data.get('credits', 0),
        design_credits=course_data.get('design_credits', 0),
        component=course_data.get('component', ''),
        description=course_data.get('description', ''),
        prerequisites=course_data.get('prerequisites', ''),
        corequisites=course_data.get('corequisites', ''),
        term=course_data.get('term', ''),
        OL=course_data.get('OL', False),
        OL_only=course_data.get('OL_only', False),
    )
    return course


# ===== END FIREBASE HELPER FUNCTIONS =====


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    """Admin interface for CustomUser"""

    list_display = (
        "username", "email", "selected_concentration",
        "has_completed_setup", "is_staff", "is_active"
    )
    list_filter = ("is_staff", "is_active", "has_completed_setup")
    search_fields = ("username", "email")
    ordering = ("username",)



# ===== FIREBASE CURRICULUM ADMIN =====


class CurriculumForm(forms.ModelForm):
    """
    Custom form for Curriculum that provides individual fields for nested JSON objects,
    except for graduation_requirements which uses PrettyJSONWidget.
    """

    # ----- Concentration Fields -----
    concentration_title = forms.CharField(
        min_length=3, max_length=100, required=False, label="Title",
        help_text="Program title (e.g., 'Software Engineering')"
    )
    concentration_code = forms.CharField(
        min_length=3, max_length=30, required=False, label="Code",
        help_text="Program code (e.g., 'INGE_SOFT'). READ-ONLY.",
        disabled=True
    )
    concentration_campus = forms.CharField(
        min_length=3, max_length=30, required=False, label="Campus",
        help_text="Campus location"
    )
    concentration_version = forms.CharField(
        max_length=30, required=False, label="Version",
        help_text="Curriculum version (e.g., '2024')"
    )

    # ----- Metadata Fields -----
    metadata_academic_years = forms.IntegerField(
        required=False, label="Academic Years",
        help_text="Total number of academic years in the program"
    )
    metadata_summer_terms = forms.IntegerField(
        required=False, label="Summer Terms",
        help_text="Number of summer terms"
    )
    metadata_start_year = forms.IntegerField(
        required=False, label="Start Year",
        help_text="Starting year of this curriculum"
    )
    metadata_trimesters_per_year = forms.IntegerField(
        required=False, label="Trimesters Per Year",
        help_text="Number of trimesters in each academic year"
    )
    metadata_total_terms = forms.IntegerField(
        required=False, label="Total Terms",
        help_text="Total number of terms in the program"
    )

    # ----- Validation Rules Fields -----
    validation_check_term_availability = forms.BooleanField(
        required=False, label="Check Term Availability",
        help_text="Validate that courses are available in selected terms"
    )
    validation_max_credits_per_term = forms.IntegerField(
        required=False, label="Max Credits Per Term",
        help_text="Maximum credit hours allowed per term"
    )
    validation_corequisite_bundling = forms.BooleanField(
        required=False, label="Corequisite Bundling",
        help_text="Enforce corequisite courses to be taken together"
    )
    validation_min_credits_for_full_time = forms.IntegerField(
        required=False, label="Min Credits for Full-Time",
        help_text="Minimum credits to be considered full-time"
    )
    validation_prerequisite_enforcement = forms.BooleanField(
        required=False, label="Prerequisite Enforcement",
        help_text="Enforce prerequisite requirements"
    )

    # ----- AI Recommendation Settings Fields -----
    ai_suggest_corequisite_bundles = forms.BooleanField(
        required=False, label="Suggest Corequisite Bundles",
        help_text="AI suggests corequisite courses together"
    )
    ai_warn_on_prerequisite_missing = forms.BooleanField(
        required=False, label="Warn on Prerequisite Missing",
        help_text="Show warnings when prerequisites are not met"
    )
    ai_default_credit_target = forms.IntegerField(
        required=False, label="Default Credit Target",
        help_text="Default target credits per term for recommendations"
    )
    ai_suggest_summer_courses = forms.BooleanField(
        required=False, label="Suggest Summer Courses",
        help_text="Include summer term suggestions"
    )
    ai_prioritize_critical_path = forms.BooleanField(
        required=False, label="Prioritize Critical Path",
        help_text="Prioritize courses on the critical graduation path"
    )
    ai_allow_overload = forms.BooleanField(
        required=False, label="Allow Overload",
        help_text="Allow recommendations that exceed max credits"
    )

    class Meta:
        model = Curriculum
        fields = ['code', 'graduation_requirements']
        widgets = {
            'graduation_requirements': PrettyJSONWidget(attrs={'rows': 18}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            # Populate Concentration fields
            concentration = self.instance.concentration or {}
            self.fields['concentration_title'].initial = concentration.get('title', '')
            self.fields['concentration_code'].initial = concentration.get('code', '')
            self.fields['concentration_campus'].initial = concentration.get('campus', '')
            self.fields['concentration_version'].initial = concentration.get('version', '')

            # Populate Metadata fields
            metadata = self.instance.metadata or {}
            self.fields['metadata_academic_years'].initial = metadata.get('academic_years')
            self.fields['metadata_summer_terms'].initial = metadata.get('summer_terms')
            self.fields['metadata_start_year'].initial = metadata.get('start_year')
            self.fields['metadata_trimesters_per_year'].initial = metadata.get('trimesters_per_year')
            self.fields['metadata_total_terms'].initial = metadata.get('total_terms')

            # Populate Validation Rules fields
            validation = self.instance.validation_rules or {}
            self.fields['validation_check_term_availability'].initial = validation.get('check_term_availability', False)
            self.fields['validation_max_credits_per_term'].initial = validation.get('max_credits_per_term')
            self.fields['validation_corequisite_bundling'].initial = validation.get('corequisite_bundling', False)
            self.fields['validation_min_credits_for_full_time'].initial = validation.get('min_credits_for_full_time')
            self.fields['validation_prerequisite_enforcement'].initial = validation.get('prerequisite_enforcement',
                                                                                        False)

            # Populate AI Settings fields
            ai_settings = self.instance.ai_recommendation_settings or {}
            self.fields['ai_suggest_corequisite_bundles'].initial = ai_settings.get('suggest_corequisite_bundles',
                                                                                    False)
            self.fields['ai_warn_on_prerequisite_missing'].initial = ai_settings.get('warn_on_prerequisite_missing',
                                                                                     False)
            self.fields['ai_default_credit_target'].initial = ai_settings.get('default_credit_target')
            self.fields['ai_suggest_summer_courses'].initial = ai_settings.get('suggest_summer_courses', False)
            self.fields['ai_prioritize_critical_path'].initial = ai_settings.get('prioritize_critical_path', False)
            self.fields['ai_allow_overload'].initial = ai_settings.get('allow_overload', False)

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Pack Concentration fields back into JSON
        instance.concentration = {
            'title': self.cleaned_data.get('concentration_title', ''),
            'code': self.cleaned_data.get('concentration_code', ''),
            'campus': self.cleaned_data.get('concentration_campus', ''),
            'version': self.cleaned_data.get('concentration_version', ''),
        }

        # Pack Metadata fields back into JSON
        instance.metadata = {
            'academic_years': self.cleaned_data.get('metadata_academic_years'),
            'summer_terms': self.cleaned_data.get('metadata_summer_terms'),
            'start_year': self.cleaned_data.get('metadata_start_year'),
            'trimesters_per_year': self.cleaned_data.get('metadata_trimesters_per_year'),
            'total_terms': self.cleaned_data.get('metadata_total_terms'),
        }

        # Pack Validation Rules fields back into JSON
        instance.validation_rules = {
            'check_term_availability': self.cleaned_data.get('validation_check_term_availability', False),
            'max_credits_per_term': self.cleaned_data.get('validation_max_credits_per_term'),
            'corequisite_bundling': self.cleaned_data.get('validation_corequisite_bundling', False),
            'min_credits_for_full_time': self.cleaned_data.get('validation_min_credits_for_full_time'),
            'prerequisite_enforcement': self.cleaned_data.get('validation_prerequisite_enforcement', False),
        }

        # Pack AI Settings fields back into JSON
        instance.ai_recommendation_settings = {
            'suggest_corequisite_bundles': self.cleaned_data.get('ai_suggest_corequisite_bundles', False),
            'warn_on_prerequisite_missing': self.cleaned_data.get('ai_warn_on_prerequisite_missing', False),
            'default_credit_target': self.cleaned_data.get('ai_default_credit_target'),
            'suggest_summer_courses': self.cleaned_data.get('ai_suggest_summer_courses', False),
            'prioritize_critical_path': self.cleaned_data.get('ai_prioritize_critical_path', False),
            'allow_overload': self.cleaned_data.get('ai_allow_overload', False),
        }

        if commit:
            instance.save()
        return instance


@admin.register(Curriculum)
class CurriculumAdmin(admin.ModelAdmin):
    """
    Admin interface for Curriculum proxy model.
    Fetches data from Firebase and saves back to Firebase.
    """
    actions = None
    form = CurriculumForm

    def concentration_name(self, obj):
        """Extract concentration title from concentration dict"""
        if isinstance(obj.concentration, dict):
            return obj.concentration.get('title', 'Unknown')
        return 'Unknown'

    concentration_name.short_description = 'Concentration'

    list_display = ('code', 'concentration_name', 'last_updated')
    search_fields = ('code',)
    readonly_fields = ('code', 'terms_display')

    fieldsets = (
        (None, {
            'fields': ('code',),
        }),
        ('Terms', {
            'fields': ('terms_display',),
            'description': 'Click on a term to view and edit its courses'
        }),
        ('Concentration', {
            'fields': (
                'concentration_title',
                'concentration_code',
                'concentration_campus',
                'concentration_version',
            ),
            'description': 'Program concentration details'
        }),
        ('Metadata', {
            'fields': (
                'metadata_academic_years',
                'metadata_summer_terms',
                'metadata_start_year',
                'metadata_trimesters_per_year',
                'metadata_total_terms',
            ),
            'classes': ('collapse',),
            'description': 'Academic calendar settings'
        }),
        ('Validation Rules', {
            'fields': (
                'validation_prerequisite_enforcement',
                'validation_corequisite_bundling',
                'validation_check_term_availability',
                ('validation_min_credits_for_full_time', 'validation_max_credits_per_term'),
            ),
            'classes': ('collapse',),
            'description': 'Course scheduling and prerequisite validation settings'
        }),
        ('AI Recommendation Settings', {
            'fields': (
                'ai_prioritize_critical_path',
                'ai_suggest_corequisite_bundles',
                'ai_warn_on_prerequisite_missing',
                'ai_suggest_summer_courses',
                'ai_allow_overload',
                'ai_default_credit_target',
            ),
            'classes': ('collapse',),
            'description': 'Settings for AI-powered course recommendations'
        }),
        ('Graduation Requirements', {
            'fields': ('graduation_requirements',),
            'classes': ('collapse',),
            'description': 'Credit requirements and component minimums (JSON format for flexible component definitions)'
        }),
    )

    def terms_display(self, obj):
        """Display list of terms with links to view courses, plus Add Term link"""
        try:
            terms_data = firebase_service.get_all_terms(obj.code)

            # Add Term link at the top
            add_term_url = reverse('admin:accounts_term_create', args=[obj.code])
            add_link = f'<p><a href="{add_term_url}" class="addlink" style="padding: 5px 10px; background: #417690; color: white; text-decoration: none; border-radius: 4px;">+ Add Term</a></p>'

            if not terms_data:
                return format_html(add_link + "<p>No terms found. Click 'Add Term' to create one.</p>")

            html_parts = [add_link]
            html_parts.append('<table style="width:100%; border-collapse: collapse; margin-top: 10px;">')
            html_parts.append('<tr style="background-color: var(--darkened-bg);">')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Term ID</th>')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Name</th>')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Year</th>')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Trimester</th>')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Credits</th>')
            html_parts.append(
                '<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); color: var(--body-fg);">Action</th>')
            html_parts.append('</tr>')

            for term_data in terms_data:
                term_id = term_data.get('term_id', '')
                term_info = term_data.get('term_info', {})
                term_name = term_info.get('term_name', '')
                year = term_info.get('year', '-')
                trimester = term_info.get('trimester', '-')
                credits = term_info.get('total_credits_in_term', '-')

                # Create composite ID for URL
                composite_id = f"{obj.code}:{term_id}"
                url = f"/admin/accounts/term/{composite_id}/change/"

                html_parts.append('<tr>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color);'
                                  f' color: var(--body-fg);">{term_id}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color);'
                                  f' color: var(--body-fg);">{term_name}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{year}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{trimester}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color);'
                                  f' color: var(--body-fg);">{credits}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color);'
                                  f'"><a href="{url}">View Term →</a></td>')
                html_parts.append('</tr>')

            html_parts.append('</table>')
            return format_html(''.join(html_parts))

        except Exception as e:
            return f"Error loading terms: {e}"

    terms_display.short_description = 'Terms in This Curriculum'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('create/', self.admin_site.admin_view(self.create_curriculum_view), name='accounts_curriculum_create'),
            path('<str:curriculum_code>/term/create/', self.admin_site.admin_view(self.create_term_view),
                 name='accounts_term_create'),
            path('<str:curriculum_code>/term/<str:term_id>/course/create/',
                 self.admin_site.admin_view(self.create_course_view), name='accounts_course_create'),
        ]
        return custom_urls + urls

    def create_curriculum_view(self, request):
        from .views import create_curriculum
        return create_curriculum(request)

    def create_term_view(self, request, curriculum_code):
        from .views import create_term
        return create_term(request, curriculum_code)

    def create_course_view(self, request, curriculum_code, term_id):
        from .views import create_course
        return create_course(request, curriculum_code, term_id)

    # def changelist_view(self, request, extra_context=None):
    #     """Add custom 'Add Curriculum' button URL to context"""
    #     extra_context = extra_context or {}
    #     extra_context['custom_add_url'] = reverse('admin:accounts_curriculum_create')
    #
    #     print(f"DEBUG: custom_add_url = {extra_context['custom_add_url']}")
    #
    #     return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        """Allow add button to show - redirects to custom view"""
        return True

    def add_view(self, request, form_url='', extra_context=None):
        """Redirect add to custom create view"""
        from django.shortcuts import redirect
        return redirect(reverse('admin:accounts_curriculum_create'))

    def has_delete_permission(self, request, obj=None):
        """Disable deleting curricula through admin for now"""
        return False

    def has_module_permission(self, request):
        """Allow module to appear in admin"""
        return True

    def get_queryset(self, request):
        """Fetch all curricula from Firebase and return as FirebaseQuerySet"""
        try:
            curricula_data = firebase_service.get_all_curricula()
            if not curricula_data:
                messages.warning(request, "No curricula found in Firebase or Firebase is unreachable.")
                return FirebaseQuerySet([], model=Curriculum)

            # Convert Firebase data to model instances
            curricula = [firebase_to_curriculum(data) for data in curricula_data]

            return FirebaseQuerySet(curricula, model=Curriculum)

        except Exception as e:
            messages.error(request, f"Error loading curricula from Firebase: {e}")
            return FirebaseQuerySet([], model=Curriculum)

    def get_object(self, request, object_id, from_field=None):
        """Fetch a single curriculum from Firebase by code"""
        try:
            curriculum_data = firebase_service.get_curriculum_metadata(object_id)
            if not curriculum_data:
                return None

            curriculum_data['code'] = object_id
            return firebase_to_curriculum(curriculum_data)

        except Exception as e:
            messages.error(request, f"Error loading curriculum: {e}")
            return None

    def save_model(self, request, obj, form, change):
        """Save curriculum data back to Firebase"""
        try:
            # Prepare data for Firebase
            curriculum_data = {
                'concentration': obj.concentration,
                'metadata': obj.metadata,
                'graduation_requirements': obj.graduation_requirements,
                'validation_rules': obj.validation_rules,
                'ai_recommendation_settings': obj.ai_recommendation_settings,
            }

            # Save to Firebase
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            curriculum_ref = db.collection('curricula').document(obj.code)
            curriculum_ref.update(curriculum_data)

            messages.success(request, f"Curriculum '{obj.code}' updated successfully in Firebase")

        except Exception as e:
            messages.error(request, f"Error saving curriculum: {e}")

    def has_delete_permission(self, request, obj=None):
        """Allow deleting curricula"""
        return True

    def delete_model(self, request, obj):
        """Delete curriculum and all its terms/courses from Firebase (cascading delete)"""
        try:
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            # Get all terms in this curriculum
            terms_ref = db.collection('curricula').document(obj.code).collection('terms')
            terms = terms_ref.stream()

            # Delete all courses in each term, then delete the term
            for term in terms:
                courses_ref = term.reference.collection('courses')
                courses = courses_ref.stream()

                # Delete all courses in this term
                for course in courses:
                    course.reference.delete()

                # Delete the term
                term.reference.delete()

            # Finally, delete the curriculum document
            db.collection('curricula').document(obj.code).delete()

            messages.success(request, f"Curriculum '{obj.code}' and all its terms/courses deleted successfully")

        except Exception as e:
            messages.error(request, f"Error deleting curriculum: {e}")


class TermForm(forms.ModelForm):
    """
    Custom form for Term that converts list fields to/from comma-separated strings.
    """

    # Override list fields with CharField for comma-separated input
    recommended_trimester = forms.CharField(
        required=False,
        label="Recommended Trimester",
        help_text="Comma-separated values (e.g., Fall, Spring)",
        widget=forms.TextInput(attrs={'style': 'width: 300px;'})
    )
    typical_next_terms = forms.CharField(
        initial='[]',
        required=False,
        label="Typical Next Terms",
        help_text="Comma-separated term IDs (e.g., term_2, term_3)",
        widget=forms.TextInput(attrs={'style': 'width: 300px;'})
    )
    critical_courses = forms.CharField(
        initial='[]',
        required=False,
        label="Critical Courses",
        help_text="Comma-separated course codes (e.g., CIIC3011, CIIC3015)",
        widget=forms.TextInput(attrs={'style': 'width: 300px;'})
    )

    class Meta:
        model = Term
        fields = [
            'curriculum_code', 'term_id', 'term_name', 'description',
            'year', 'trimester', 'recommended_trimester', 'typical_next_terms',
            'min_credits', 'max_credits', 'total_credits_in_term', 'critical_courses'
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            # Convert lists to comma-separated strings for display
            rec_trim = self.instance.recommended_trimester
            if isinstance(rec_trim, list):
                self.fields['recommended_trimester'].initial = ', '.join(str(x) for x in rec_trim)

            next_terms = self.instance.typical_next_terms
            if isinstance(next_terms, list):
                self.fields['typical_next_terms'].initial = ', '.join(str(x) for x in next_terms)

            crit_courses = self.instance.critical_courses
            if isinstance(crit_courses, list):
                self.fields['critical_courses'].initial = ', '.join(str(x) for x in crit_courses)

    def clean_recommended_trimester(self):
        """Convert comma-separated string to list"""
        value = self.cleaned_data.get('recommended_trimester', '')
        if not value:
            return []
        return [x.strip() for x in value.split(',') if x.strip()]

    def clean_typical_next_terms(self):
        """Convert comma-separated string to list"""
        value = self.cleaned_data.get('typical_next_terms', '')
        if not value:
            return []
        return [x.strip() for x in value.split(',') if x.strip()]

    def clean_critical_courses(self):
        """Convert comma-separated string to list"""
        value = self.cleaned_data.get('critical_courses', '')
        if not value:
            return []
        return [x.strip() for x in value.split(',') if x.strip()]


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    """
    Admin interface for Term proxy model.
    Fetches data from Firebase and saves back to Firebase.
    """
    actions = None
    form = TermForm

    list_display = ('term_id', 'term_name', 'curriculum_code', 'year', 'trimester', 'total_credits_in_term')
    list_filter = ('curriculum_code', 'year', 'trimester')
    search_fields = ('term_id', 'term_name')

    readonly_fields = ('courses_display',)

    fieldsets = (
        ('Courses in This Term', {
            'fields': ('courses_display',),
            'description': 'All courses available in this term'
        }),
        ('Term Information', {
            'fields': ('curriculum_code', 'term_id', 'term_name', 'description')
        }),
        ('Schedule', {
            'fields': ('year', 'trimester', 'recommended_trimester', 'typical_next_terms')
        }),
        ('Credits', {
            'fields': ('min_credits', 'max_credits', 'total_credits_in_term')
        }),
        ('Critical Courses', {
            'fields': ('critical_courses',)
        }),
    )

    def courses_display(self, obj):
        """Display list of courses in this term, plus Add Course link"""
        
        # STOP if term not saved yet
        if not obj or not obj.curriculum_code or not obj.term_id:
            return "Save the term to add courses."

        try:
            courses_data = firebase_service.get_term_courses(obj.curriculum_code, obj.term_id)

            # Add Course link at the top
            add_course_url = reverse('admin:accounts_course_create', args=[obj.curriculum_code, obj.term_id])
            add_link = f'<p><a href="{add_course_url}" class="addlink" style="padding: 5px 10px; background: #417690; color: white; text-decoration: none; border-radius: 4px;">+ Add Course</a></p>'

            if not courses_data:
                return format_html(add_link + "<p>No courses found. Click 'Add Course' to create one.</p>")

            html_parts = [add_link]
            html_parts.append('<table style="width:100%; border-collapse: collapse; margin-top: 10px;">')
            html_parts.append('<tr style="background-color: var(--darkened-bg);">')
            html_parts.append('<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); '
                              'color: var(--body-fg);">Code</th>')
            html_parts.append('<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); '
                              'color: var(--body-fg);">Title</th>')
            html_parts.append('<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color); '
                              'color: var(--body-fg);">Credits</th>')
            html_parts.append('<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color);'
                              ' color: var(--body-fg);">Component</th>')
            html_parts.append('<th style="padding: 8px; text-align: left; border: 1px solid var(--hairline-color);'
                              ' color: var(--body-fg);">Action</th>')
            html_parts.append('</tr>')

            for course_data in courses_data:
                code = course_data.get('code', '')
                title = course_data.get('title', '')
                credits = course_data.get('credits', '-')
                component = course_data.get('component', '-')

                # Create composite ID for URL
                composite_id = f"{obj.curriculum_code}:{obj.term_id}:{code}"
                url = f"/admin/accounts/course/{composite_id}/change/"

                html_parts.append('<tr>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{code}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{title}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{credits}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color); '
                                  f'color: var(--body-fg);">{component}</td>')
                html_parts.append(f'<td style="padding: 8px; border: 1px solid var(--hairline-color);">'
                                  f'<a href="{url}">Edit →</a></td>')
                html_parts.append('</tr>')

            html_parts.append('</table>')
            return format_html(''.join(html_parts))

        except Exception as e:
            return f"Error loading courses: {e}"

    courses_display.short_description = 'Courses'

    def get_readonly_fields(self, request, obj=None):
        """
        Make curriculum_code and term_id readonly when editing.
        When adding a new term, these fields are editable.
        """
        if obj:  # editing existing term
            return ('curriculum_code', 'term_id', 'courses_display')
        return ('courses_display',)  # creating new term

    def get_queryset(self, request):
        """Fetch all terms from all curricula and return as FirebaseQuerySet"""
        try:
            # Get all curricula first
            curricula_data = firebase_service.get_all_curricula()
            if not curricula_data:
                messages.warning(request, "No curricula found in Firebase or Firebase is unreachable.")
                return FirebaseQuerySet([], model=Term)

            all_terms = []

            # For each curriculum, get all its terms
            for curriculum_data in curricula_data:
                curriculum_code = curriculum_data.get('code', '')
                terms_data = firebase_service.get_all_terms(curriculum_code)

                # Convert each term to model instance
                for term_data in terms_data:
                    term = firebase_to_term(term_data, curriculum_code)
                    all_terms.append(term)

            if not all_terms:
                messages.info(request, "No terms found in any curriculum.")

            return FirebaseQuerySet(all_terms, model=Term)

        except Exception as e:
            messages.error(request, f"Error loading terms from Firebase: {e}")
            return FirebaseQuerySet([], model=Term)

    def get_object(self, request, object_id, from_field=None):
        """
        Fetch a single term from Firebase.
        object_id format: {curriculum_code}:{term_id}
        """
        try:
            # Split composite ID
            parts = object_id.split(':', 1)
            if len(parts) != 2:
                return None

            curriculum_code, term_id = parts

            # Get term data from Firebase
            terms_data = firebase_service.get_all_terms(curriculum_code)

            # Find the specific term
            for term_data in terms_data:
                if term_data.get('term_id') == term_id:
                    return firebase_to_term(term_data, curriculum_code)

            return None

        except Exception as e:
            messages.error(request, f"Error loading term: {e}")
            return None

    def save_model(self, request, obj, form, change):
        """Save term data back to Firebase"""
        try:
            # Build term_info object with updated values
            term_info = {
                'term_name': obj.term_name,
                'description': obj.description,
                'year': obj.year,
                'trimester': obj.trimester,
                'recommended_trimester': form.cleaned_data.get('recommended_trimester', []),
                'typical_next_terms': form.cleaned_data.get('typical_next_terms', []),
                'critical_courses': form.cleaned_data.get('critical_courses', []),
                'min_credits': obj.min_credits,
                'max_credits': obj.max_credits,
                'total_credits_in_term': obj.total_credits_in_term,
            }

            # Save to Firebase
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            term_ref = (db.collection('curricula')
                        .document(obj.curriculum_code)
                        .collection('terms')
                        .document(obj.term_id))

            term_data = {
                'term_id': obj.term_id,
                'term_info': term_info,
            }

            # Check if document exists
            doc = term_ref.get()

            if doc.exists:
                term_ref.update(term_data)
                messages.success(request, f"Term '{obj.term_id}' updated successfully in Firebase")
            else:
                term_ref.set(term_data)
                messages.success(request, f"Term '{obj.term_id}' created successfully in Firebase")

        except Exception as e:
            messages.error(request, f"Error saving term: {e}")

    def has_add_permission(self, request):
        """Disable adding terms through admin for now"""
        return True

    def has_delete_permission(self, request, obj=None):
        """Allow deleting terms"""
        return True

    def delete_model(self, request, obj):
        """Delete term and all its courses from Firebase (cascading delete)"""
        try:
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            # Get reference to this term
            term_ref = (db.collection('curricula')
                        .document(obj.curriculum_code)
                        .collection('terms')
                        .document(obj.term_id))

            # Delete all courses in this term
            courses_ref = term_ref.collection('courses')
            courses = courses_ref.stream()

            for course in courses:
                course.reference.delete()

            # Delete the term
            term_ref.delete()

            messages.success(request, f"Term '{obj.term_id}' and all its courses deleted successfully")

        except Exception as e:
            messages.error(request, f"Error deleting term: {e}")

    def has_change_permission(self, request, obj=None):
        """Allow editing terms"""
        return True

    def response_add(self, request, obj, post_url_continue=None):
        """Redirect after creating a Term"""
        composite_id = f"{obj.curriculum_code}:{obj.term_id}"
        return redirect(f"/admin/accounts/term/{composite_id}/change/")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    """
    Admin interface for Course proxy model.
    Fetches data from Firebase and saves back to Firebase.
    """
    actions = None
    list_display = ('code', 'title', 'credits', 'curriculum_code', 'term_id',
                    'component', 'OL', 'prerequisites')
    list_filter = ('curriculum_code', 'term_id', 'component', 'OL', 'OL_only')
    search_fields = ('code', 'title', 'description')

    fieldsets = (
        ('Basic Information', {
            'fields': ('curriculum_code', 'term_id', 'code', 'title', 'component')
        }),
        ('Credits', {
            'fields': ('credits', 'design_credits')
        }),
        ('Description', {
            'fields': ('description',)
        }),
        ('Requirements', {
            'fields': ('prerequisites', 'corequisites', 'term')
        }),
        ('Online Availability', {
            'fields': ('OL', 'OL_only')
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        """
        Only make curriculum_code, term_id, code readonly if editing an existing course.
        When adding, admin must enter them.
        """
        if obj:  # editing
            return ('curriculum_code', 'term_id', 'code')
        return ()  # creating -> all editable

    def get_queryset(self, request):
        """Fetch all courses from all terms in all curricula and return as FirebaseQuerySet"""
        try:
            # Get all curricula first
            curricula_data = firebase_service.get_all_curricula()
            if not curricula_data:
                messages.warning(request, "No curricula found in Firebase or Firebase is unreachable.")
                return FirebaseQuerySet([], model=Course)

            all_courses = []

            # For each curriculum, get all its terms
            for curriculum_data in curricula_data:
                curriculum_code = curriculum_data.get('code', '')
                terms_data = firebase_service.get_all_terms(curriculum_code)

                # For each term, get all its courses
                for term_data in terms_data:
                    term_id = term_data.get('term_id', '')
                    courses_data = firebase_service.get_term_courses(curriculum_code, term_id)

                    # Convert each course to model instance
                    for course_data in courses_data:
                        course = firebase_to_course(course_data, curriculum_code, term_id)
                        all_courses.append(course)

            if not all_courses:
                messages.info(request, "No courses found in any term.")

            return FirebaseQuerySet(all_courses, model=Course)

        except Exception as e:
            messages.error(request, f"Error loading courses from Firebase: {e}")
            return FirebaseQuerySet([], model=Course)

    def get_object(self, request, object_id, from_field=None):
        """
        Fetch a single course from Firebase.
        object_id format: {curriculum_code}:{term_id}:{course_code}
        """
        try:
            # Split composite ID
            parts = object_id.split(':', 2)
            if len(parts) != 3:
                return None

            curriculum_code, term_id, course_code = parts

            # Get courses from this term
            courses_data = firebase_service.get_term_courses(curriculum_code, term_id)

            # Find the specific course
            for course_data in courses_data:
                if course_data.get('code') == course_code or course_data.get('id') == course_code:
                    return firebase_to_course(course_data, curriculum_code, term_id)

            return None

        except Exception as e:
            messages.error(request, f"Error loading course: {e}")
            return None

    def save_model(self, request, obj, form, change):
        """Save course data back to Firebase - only update changed fields"""
        try:
            # Prepare data for Firebase - only include changed fields
            course_data = {}

            if 'code' in form.changed_data:
                course_data['code'] = obj.code
            if 'title' in form.changed_data:
                course_data['title'] = obj.title
            if 'credits' in form.changed_data:
                course_data['credits'] = obj.credits
            if 'design_credits' in form.changed_data:
                course_data['design_credits'] = obj.design_credits
            if 'component' in form.changed_data:
                course_data['component'] = obj.component
            if 'description' in form.changed_data:
                course_data['description'] = obj.description
            if 'prerequisites' in form.changed_data:
                course_data['prerequisites'] = obj.prerequisites
            if 'corequisites' in form.changed_data:
                course_data['corequisites'] = obj.corequisites
            if 'term' in form.changed_data:
                course_data['term'] = obj.term
            if 'OL' in form.changed_data:
                course_data['OL'] = obj.OL
            if 'OL_only' in form.changed_data:
                course_data['OL_only'] = obj.OL_only

            # Only update if there are changes
            if not course_data:
                messages.info(request, "No changes detected")
                return

            # Save to Firebase
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            course_ref = (db.collection('curricula')
                          .document(obj.curriculum_code)
                          .collection('terms')
                          .document(obj.term_id)
                          .collection('courses')
                          .document(obj.code))

            course_ref.update(course_data)

            messages.success(request, f"Course '{obj.code}' updated successfully in Firebase")

        except Exception as e:
            messages.error(request, f"Error saving course: {e}")

    def has_add_permission(self, request):
        """Disable adding courses through admin for now"""
        return False

    def has_change_permission(self, request, obj=None):
        """Allow editing terms"""
        return True

    def response_add(self, request, obj, post_url_continue=None):
        """
        Override to redirect correctly after adding a course in Firebase.
        """
        try:
            # Build the composite ID that get_object expects
            obj_id = f"{obj.curriculum_code}:{obj.term_id}:{obj.code}"

            return HttpResponseRedirect(
                reverse('admin:accounts_course_change', args=[obj_id])
            )

        except Exception as e:
            messages.error(request, f"Could not redirect after adding: {e}")
            # fallback to default Django behavior
            return super().response_add(request, obj, post_url_continue)

    def has_delete_permission(self, request, obj=None):
        """Allow deleting courses"""
        return True

    def has_module_permission(self, request):
        """Allow module to appear in admin"""
        return True

    def delete_model(self, request, obj):
        """Delete course from Firebase"""
        try:
            db = firebase_service.db
            if not db:
                messages.error(request, "Firebase connection not available")
                return

            course_ref = (db.collection('curricula')
                          .document(obj.curriculum_code)
                          .collection('terms')
                          .document(obj.term_id)
                          .collection('courses')
                          .document(obj.code))

            course_ref.delete()
            messages.success(request, f"Course '{obj.code}' deleted successfully from Firebase")

        except Exception as e:
            messages.error(request, f"Error deleting course: {e}")