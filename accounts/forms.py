#forms.py

from django import forms
from accounts.models import CustomUser

from .widgets import PrettyJSONWidget

class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(label='Password', widget=forms.PasswordInput, min_length=3, max_length=100)
    password2 = forms.CharField(label='Repeat password', widget=forms.PasswordInput, min_length=3, max_length=100)
    email = forms.EmailField(label='Email', required=True, min_length=4, max_length=100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].max_length = 30
        self.fields['username'].min_length = 3

    class Meta:
        model = CustomUser
        fields = ('username', 'email')

    def clean_email(self):
        email = self.cleaned_data.get('email')
        print(f"clean_email called with: {email}")  # DEBUG
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError('A user with that email already exists.')
        return email

    def clean_password2(self):
        cd = self.cleaned_data
        if cd.get('password') != cd.get('password2'):
            raise forms.ValidationError('Passwords don\'t match.')
        return cd.get('password2')


# ============================================================
# CURRICULUM MANAGEMENT FORMS (Plain forms - no ORM)
# ============================================================

class CurriculumCreateForm(forms.Form):
    """
    Form for creating a new curriculum in Firebase.
    Uses plain Form (not ModelForm) to avoid Django ORM.
    """
    code = forms.CharField(
        min_length=3,
        max_length=30,
        required=True,
        help_text="Unique identifier, e.g., 'Comp_Eng_2025' or 'Software_Eng_2026'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    concentration_title = forms.CharField(
        min_length=3,
        max_length=100,
        required=True,
        label="Program Title",
        help_text="e.g., 'Computer Engineering' or 'Software Engineering'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    concentration_code = forms.CharField(
        min_length=3,
        max_length=30,
        required=True,
        label="Concentration Code",
        help_text="e.g., 'COMP_ENG' or 'SOFT_ENG'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    concentration_campus = forms.CharField(
        min_length=3,
        max_length=30,
        required=False,
        label="Campus",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    concentration_version = forms.CharField(
        max_length=30,
        required=True,
        label="Version",
        help_text="e.g., '2025'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )

    # Metadata
    metadata_academic_years = forms.IntegerField(
        min_value=0,
        required=False,
        label="Academic Years",
        help_text="Total years in the program (e.g., 4 or 5)",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    metadata_summer_terms = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label="Number of summer terms",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    metadata_start_year = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label="Starting year of this curriculum",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    metadata_trimesters_per_year = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label="Number of trimesters in each academic year",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    metadata_total_terms = forms.IntegerField(
        min_value=0,
        required=False,
        label="Total Terms",
        help_text="Total number of terms in the program",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )

    # Validation rules
    validation_prerequisite_enforcement = forms.BooleanField(
        initial=False,
        required=False,
        label="Enforce prerequisite requirements",
    )
    validation_corequisite_bundling = forms.BooleanField(
        initial=False,
        required=False,
        label="Enforce corequisite courses to be taken together",
    )
    validation_min_credits_for_full_time = forms.IntegerField(
        min_value=0,
        required=False,
        label="Minimum credits to be considered full-time",
    )
    validation_max_credits_per_term = forms.IntegerField(
        min_value=0,
        required=False,
        label="Total Terms",
        help_text="Total number of terms in the program",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    validation_check_term_availability = forms.BooleanField(
        initial=False,
        required=False,
        label="Check Term Availability",
        help_text="Validate that courses are available in selected terms",
    )

    # AI Recommendation Settings
    ai_prioritize_critical_path = forms.BooleanField(
        initial=True,
        required=False,
        label="Prioritize courses on the critical graduation path",
    )
    ai_suggest_corequisite_bundles = forms.BooleanField(
        initial=True,
        required=False,
        label="AI suggests corequisite courses together",
    )
    ai_warn_on_prerequisite_missing = forms.BooleanField(
        initial=True,
        required=False,
        label="Show warnings when prerequisites are not met",
    )
    ai_suggest_summer_courses = forms.BooleanField(
        initial=False,
        required=False,
        label="Include summer term suggestions",
    )
    ai_allow_overload = forms.BooleanField(
        initial=False,
        required=False,
        label="Allow recommendations that exceed max credits",
    )
    ai_default_credit_target = forms.IntegerField(
        min_value=0,
        initial=15,
        required=False,
        label="Default target credits per term for recommendations",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )

    # Graduation Requirements
    graduation_requirements = forms.CharField(
        initial=[],
        required=False,
        label="Credit requirements and component minimums (JSON format)",
        widget=PrettyJSONWidget(attrs={'rows': 18}),
    )

    def __init__(self, *args, **kwargs):
        self.firebase_service = kwargs.pop('firebase_service', None)
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data.get('code')
        if self.firebase_service and self.firebase_service.curriculum_exists(code):
            raise forms.ValidationError(f"A curriculum with code '{code}' already exists.")
        return code

    def clean_graduation_requirements(self):
        """Parse JSON string to Python object"""
        import json
        value = self.cleaned_data.get('graduation_requirements', '')
        if not value or value == '[]':
            return []
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise forms.ValidationError("Invalid JSON format for graduation requirements.")


class TermCreateForm(forms.Form):
    """
    Form for creating a new term in a curriculum.
    Curriculum code is passed via URL, not form field.
    """
    term_id = forms.CharField(
        min_length=3,
        max_length=30,
        required=True,
        help_text="Unique identifier within curriculum, e.g., 'term_1', 'term_2', 'prep_term'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    term_name = forms.CharField(
        min_length=3,
        max_length=100,
        required=True,
        help_text="Display name, e.g., 'First Year - Fall' or 'Preparatory Term'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    description = forms.CharField(
        min_length=3,
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={'class': 'vLargeTextField', 'rows': 3})
    )
    year = forms.IntegerField(
        min_value=0,
        required=False,
        help_text="Academic year number (1, 2, 3, etc.)",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    trimester = forms.CharField(
        min_length=3,
        max_length=30,
        required=False,
        help_text="e.g., 'Fall', 'Spring', 'Summer'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
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
    min_credits = forms.IntegerField(
        min_value=0,
        required=False,
        label="Minimum Credits",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    max_credits = forms.IntegerField(
        min_value=0,
        required=False,
        label="Maximum Credits",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    critical_courses = forms.CharField(
        required=False,
        label="Critical Courses",
        help_text="Comma-separated course codes (e.g., CIIC3011, CIIC3015)",
        widget=forms.TextInput(attrs={'style': 'width: 300px;'})
    )
    total_credits_in_term = forms.CharField(
        required=False,
        label="Total Credits In Term",
        help_text="Comma-separated course codes (e.g., CIIC3011, CIIC3015)",
        widget=forms.TextInput(attrs={'style': 'width: 300px;'})
    )

    def __init__(self, *args, **kwargs):
        self.firebase_service = kwargs.pop('firebase_service', None)
        self.curriculum_code = kwargs.pop('curriculum_code', None)
        super().__init__(*args, **kwargs)

    def clean_term_id(self):
        term_id = self.cleaned_data.get('term_id')
        if self.firebase_service and self.curriculum_code:
            if self.firebase_service.term_exists(self.curriculum_code, term_id):
                raise forms.ValidationError(
                    f"A term with ID '{term_id}' already exists in curriculum '{self.curriculum_code}'."
                )
        return term_id

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


class CourseCreateForm(forms.Form):
    """
    Form for creating a new course in a term.
    Curriculum code and term_id are passed via URL.
    """
    code = forms.CharField(
        min_length=3,
        max_length=30,
        required=True,
        label="Course Code",
        help_text="e.g., 'MATH1350', 'CIIC3011'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    title = forms.CharField(
        min_length=3,
        max_length=100,
        required=True,
        label="Course Title",
        help_text="e.g., 'Calculus I', 'Introduction to Computer Programming'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    credits = forms.IntegerField(
        min_value=0,
        initial=3,
        required=True,
        help_text="Credit hours (typically 3 or 4)",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    design_credits = forms.IntegerField(
        min_value=0,
        initial=0,
        required=False,
        label="Design Credits",
        widget=forms.NumberInput(attrs={'class': 'vIntegerField'})
    )
    component = forms.CharField(
        max_length=150,
        required=False,
        help_text="e.g., 'Core', 'Elective', 'Math', 'Science'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    description = forms.CharField(
        min_length=3,
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={'class': 'vLargeTextField', 'rows': 3})
    )
    prerequisites = forms.CharField(
        max_length=500,
        required=False,
        help_text="Comma-separated course codes, e.g., 'MATH1350, CIIC3011'",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    corequisites = forms.CharField(
        max_length=500,
        required=False,
        help_text="Comma-separated course codes",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    term = forms.CharField(
        max_length=50,
        required=False,

        label="Term",
        help_text="Term identifier for this course offering",
        widget=forms.TextInput(attrs={'class': 'vTextField'})
    )
    OL = forms.BooleanField(
        required=False,
        label="Available Online",
        help_text="Check if this course is available online"
    )
    OL_only = forms.BooleanField(
        required=False,
        label="Online Only",
        help_text="Check if this course is ONLY available online"
    )

    def __init__(self, *args, **kwargs):
        self.firebase_service = kwargs.pop('firebase_service', None)
        self.curriculum_code = kwargs.pop('curriculum_code', None)
        self.term_id = kwargs.pop('term_id', None)
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data.get('code')
        if self.firebase_service and self.curriculum_code and self.term_id:
            if self.firebase_service.course_exists_in_term(self.curriculum_code, self.term_id, code):
                raise forms.ValidationError(
                    f"A course with code '{code}' already exists in this term."
                )
        return code

    def clean_prerequisites(self):
        prerequisites = self.cleaned_data.get('prerequisites', '')
        if prerequisites and self.firebase_service and self.curriculum_code:
            is_valid, missing = self.firebase_service.validate_prerequisites(
                self.curriculum_code, prerequisites
            )
            if not is_valid:
                raise forms.ValidationError(
                    f"These prerequisite courses don't exist in the curriculum: {', '.join(missing)}"
                )
        return prerequisites