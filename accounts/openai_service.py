"""
OpenAI wrapper for PolyTech Companion with full database access.

This enhanced version includes:
- Direct database access for real student data
- Course catalog queries
- Prerequisite checking
- Degree requirement tracking
- Real-time enrollment status
"""
import logging
import os
import json
import re
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from html import escape
from django.db.models import Q, Count, Avg
from django.core.cache import cache

logger = logging.getLogger(__name__)

# OpenAI imports
try:
    import openai as openai_pkg
except Exception:
    openai_pkg = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4")
DEFAULT_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
DEFAULT_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1500"))

_client = None
print(f"DEBUG: OPENAI_API_KEY exists: {bool(OPENAI_API_KEY)}")
print(f"DEBUG: OpenAI class available: {OpenAI is not None}")
if OPENAI_API_KEY:
    try:
        if OpenAI is not None:
            _client = OpenAI(api_key=OPENAI_API_KEY)
            print(f"DEBUG: Client created successfully: {_client is not None}")
        elif openai_pkg is not None:
            openai_pkg.api_key = OPENAI_API_KEY
            _client = openai_pkg
    except Exception as e:
        print(f"DEBUG: Failed to create client: {e}")
        logger.exception("Failed to initialize OpenAI client: %s", e)
else:
    print("DEBUG: No API key found")
    logger.debug("OPENAI_API_KEY not set; OpenAI calls will fail if invoked.")


# Memory service import
try:
    from . import memory_service
except Exception:
    memory_service = None


class DatabaseContextProvider:
    """
    Provides real database context to the AI for accurate recommendations
    """
    
    def __init__(self):
        self.cache_timeout = 300  # 5 minutes cache
    
    def get_student_profile(self, user):
        """Get comprehensive student profile from database"""
        try:
            from django.contrib.auth.models import User
            
            # Try to get from cache first
            cache_key = f"student_profile_{user.id}"
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data
            
            profile_data = {
                'student_id': user.username,
                'name': f"{user.first_name} {user.last_name}".strip() or user.username,
                'email': user.email,
            }
            
            # Get additional profile info if exists
            if hasattr(user, 'profile'):
                profile = user.profile
                profile_data.update({
                    'major': getattr(profile, 'major', 'Undeclared'),
                    'year': getattr(profile, 'year', 'Freshman'),
                    'advisor': getattr(profile, 'advisor', 'Not Assigned'),
                    'enrollment_date': str(getattr(profile, 'enrollment_date', '')),
                })
            
            cache.set(cache_key, profile_data, self.cache_timeout)
            return profile_data
            
        except Exception as e:
            logger.error(f"Error getting student profile: {e}")
            return {}
    
    def get_student_courses(self, user):
        """Get student's enrolled and completed courses with grades"""
        try:
            # Import your actual models - adjust these based on your models.py
            from accounts.models import CourseEnrollment, Course
            
            cache_key = f"student_courses_{user.id}"
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data
            
            # Get all enrollments for the student
            enrollments = CourseEnrollment.objects.filter(
                student=user
            ).select_related('course').order_by('-semester', 'course__code')
            
            completed_courses = []
            current_courses = []
            failed_courses = []
            total_credits = 0
            grade_points = 0
            graded_credits = 0
            
            grade_values = {
                'A': 4.0, 'B': 3.0, 'C': 2.0, 'D': 1.0, 'F': 0.0,
                'A-': 3.7, 'B+': 3.3, 'B-': 2.7, 'C+': 2.3, 'C-': 1.7, 'D+': 1.3, 'D-': 0.7
            }
            
            for enrollment in enrollments:
                course_info = {
                    'code': enrollment.course.code,
                    'name': enrollment.course.name,
                    'credits': enrollment.course.credits,
                    'semester': enrollment.semester,
                    'year': enrollment.year,
                    'grade': enrollment.grade,
                    'status': enrollment.status
                }
                
                if enrollment.status == 'completed':
                    if enrollment.grade and enrollment.grade != 'W':  # W = Withdrawn
                        if enrollment.grade == 'F':
                            failed_courses.append(course_info)
                        else:
                            completed_courses.append(course_info)
                            total_credits += enrollment.course.credits
                        
                        # Calculate GPA
                        if enrollment.grade in grade_values:
                            grade_points += grade_values[enrollment.grade] * enrollment.course.credits
                            graded_credits += enrollment.course.credits
                            
                elif enrollment.status in ['enrolled', 'in_progress']:
                    current_courses.append(course_info)
            
            gpa = round(grade_points / graded_credits, 2) if graded_credits > 0 else 0.0
            
            result = {
                'completed_courses': completed_courses,
                'current_courses': current_courses,
                'failed_courses': failed_courses,
                'total_credits': total_credits,
                'gpa': gpa,
                'academic_standing': self._get_academic_standing(gpa),
                'credits_in_progress': sum(c['credits'] for c in current_courses)
            }
            
            cache.set(cache_key, result, self.cache_timeout)
            return result
            
        except Exception as e:
            logger.error(f"Error getting student courses: {e}")
            return {
                'completed_courses': [],
                'current_courses': [],
                'failed_courses': [],
                'total_credits': 0,
                'gpa': 0.0
            }
    
    def _get_academic_standing(self, gpa):
        """Determine academic standing based on GPA"""
        if gpa >= 3.5:
            return "Dean's List"
        elif gpa >= 3.0:
            return "Good Standing"
        elif gpa >= 2.0:
            return "Satisfactory"
        elif gpa >= 1.5:
            return "Academic Warning"
        else:
            return "Academic Probation"
    
    def get_available_courses(self, user=None, semester='Spring 2025', major=None):
        """Get available courses for registration with filtering"""
        try:
            from accounts.models import Course, CourseOffering, CourseEnrollment
            
            # Get current semester offerings
            offerings = CourseOffering.objects.filter(
                semester=semester,
                seats_available__gt=0
            ).select_related('course')
            
            # Filter by major if specified
            if major:
                offerings = offerings.filter(
                    Q(course__department=major) | 
                    Q(course__is_general_education=True)
                )
            
            # Get courses student has already taken
            taken_courses = []
            if user:
                taken_courses = CourseEnrollment.objects.filter(
                    student=user,
                    status__in=['completed', 'enrolled', 'in_progress']
                ).values_list('course__code', flat=True)
            
            available = []
            for offering in offerings[:30]:  # Limit to prevent token overflow
                # Skip if already taken
                if offering.course.code in taken_courses:
                    continue
                
                # Check prerequisites
                prereq_met = True
                prereq_list = []
                if user and offering.course.prerequisites.exists():
                    prereq_met, prereq_list = self._check_prerequisites(
                        user, offering.course
                    )
                
                available.append({
                    'code': offering.course.code,
                    'name': offering.course.name,
                    'credits': offering.course.credits,
                    'department': offering.course.department,
                    'description': offering.course.description[:200],
                    'prerequisites': prereq_list,
                    'prerequisites_met': prereq_met,
                    'seats_available': offering.seats_available,
                    'schedule': offering.schedule,
                    'instructor': offering.instructor,
                    'is_online': offering.is_online,
                    'is_general_education': offering.course.is_general_education
                })
            
            return available
            
        except Exception as e:
            logger.error(f"Error getting available courses: {e}")
            return []
    
    def _check_prerequisites(self, user, course):
        """Check if student meets course prerequisites"""
        try:
            from accounts.models import CourseEnrollment
            
            prereqs = course.prerequisites.all()
            if not prereqs:
                return True, []
            
            completed = CourseEnrollment.objects.filter(
                student=user,
                status='completed',
                grade__in=['A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'D-']
            ).values_list('course__code', flat=True)
            
            prereq_list = []
            all_met = True
            
            for prereq in prereqs:
                met = prereq.code in completed
                if not met:
                    all_met = False
                prereq_list.append({
                    'code': prereq.code,
                    'name': prereq.name,
                    'met': met
                })
            
            return all_met, prereq_list
            
        except Exception as e:
            logger.error(f"Error checking prerequisites: {e}")
            return True, []
    
    def get_degree_requirements(self, user):
        """Get degree requirements and student's progress"""
        try:
            from accounts.models import DegreeRequirement, CourseEnrollment
            
            cache_key = f"degree_requirements_{user.id}"
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data
            
            # Get student's major
            major = 'Computer Science'  # Default
            if hasattr(user, 'profile'):
                major = getattr(user.profile, 'major', major)
            
            # Get all requirements for the major
            requirements = DegreeRequirement.objects.filter(
                major=major
            ).prefetch_related('required_courses')
            
            # Get student's completed courses
            completed = CourseEnrollment.objects.filter(
                student=user,
                status='completed',
                grade__in=['A', 'A-', 'B+', 'B', 'B-', 'C+', 'C', 'C-', 'D+', 'D', 'D-']
            ).values_list('course__code', flat=True)
            
            req_data = []
            total_required = 0
            total_completed = 0
            
            for req in requirements:
                required_courses = req.required_courses.all()
                completed_in_category = []
                remaining_in_category = []
                
                for course in required_courses:
                    if course.code in completed:
                        completed_in_category.append({
                            'code': course.code,
                            'name': course.name,
                            'credits': course.credits
                        })
                    else:
                        remaining_in_category.append({
                            'code': course.code,
                            'name': course.name,
                            'credits': course.credits
                        })
                
                category_required = req.credits_required or len(required_courses) * 3
                category_completed = sum(c['credits'] for c in completed_in_category)
                
                total_required += category_required
                total_completed += category_completed
                
                req_data.append({
                    'category': req.category,
                    'description': req.description,
                    'credits_required': category_required,
                    'credits_completed': category_completed,
                    'completed_courses': completed_in_category,
                    'remaining_courses': remaining_in_category,
                    'progress_percentage': round((category_completed / category_required * 100), 1) if category_required > 0 else 0
                })
            
            result = {
                'major': major,
                'total_credits_required': total_required,
                'total_credits_completed': total_completed,
                'overall_progress': round((total_completed / total_required * 100), 1) if total_required > 0 else 0,
                'requirements': req_data,
                'estimated_graduation': self._estimate_graduation(total_required - total_completed)
            }
            
            cache.set(cache_key, result, self.cache_timeout)
            return result
            
        except Exception as e:
            logger.error(f"Error getting degree requirements: {e}")
            return {
                'major': 'Unknown',
                'total_credits_required': 120,
                'total_credits_completed': 0,
                'requirements': []
            }
    
    def _estimate_graduation(self, remaining_credits):
        """Estimate graduation date based on remaining credits"""
        avg_credits_per_semester = 15
        semesters_remaining = (remaining_credits + avg_credits_per_semester - 1) // avg_credits_per_semester
        
        current_date = datetime.now()
        current_month = current_date.month
        
        # Determine current semester
        if 1 <= current_month <= 5:
            current_semester = "Spring"
        elif 6 <= current_month <= 7:
            current_semester = "Summer"
        else:
            current_semester = "Fall"
        
        # Calculate estimated graduation
        year = current_date.year
        semester = current_semester
        
        for _ in range(semesters_remaining):
            if semester == "Spring":
                semester = "Fall"
            elif semester == "Fall":
                semester = "Spring"
                year += 1
            else:  # Summer
                semester = "Fall"
        
        return f"{semester} {year}"
    
    def get_course_recommendations(self, user, limit=5):
        """Get personalized course recommendations based on student history"""
        try:
            from accounts.models import Course, CourseEnrollment
            
            # Get student's completed courses
            completed = CourseEnrollment.objects.filter(
                student=user,
                status='completed'
            ).values_list('course__code', flat=True)
            
            # Get student's major
            major = 'Computer Science'
            if hasattr(user, 'profile'):
                major = getattr(user.profile, 'major', major)
            
            # Find courses that:
            # 1. Student hasn't taken
            # 2. Prerequisites are met
            # 3. Are in student's major or general education
            recommendations = []
            
            potential_courses = Course.objects.filter(
                Q(department=major) | Q(is_general_education=True)
            ).exclude(
                code__in=completed
            ).prefetch_related('prerequisites')
            
            for course in potential_courses:
                # Check prerequisites
                prereqs_met = True
                for prereq in course.prerequisites.all():
                    if prereq.code not in completed:
                        prereqs_met = False
                        break
                
                if prereqs_met:
                    # Calculate recommendation score
                    score = 0
                    
                    # Higher score for major courses
                    if course.department == major:
                        score += 10
                    
                    # Higher score for lower level courses (should take first)
                    level = int(course.code[-4:-3]) if course.code[-4:-3].isdigit() else 1
                    score += (5 - level) * 2
                    
                    # Add to recommendations
                    recommendations.append({
                        'course': course,
                        'score': score,
                        'reason': self._get_recommendation_reason(course, major)
                    })
            
            # Sort by score and return top recommendations
            recommendations.sort(key=lambda x: x['score'], reverse=True)
            
            return [
                {
                    'code': rec['course'].code,
                    'name': rec['course'].name,
                    'credits': rec['course'].credits,
                    'reason': rec['reason'],
                    'description': rec['course'].description[:150]
                }
                for rec in recommendations[:limit]
            ]
            
        except Exception as e:
            logger.error(f"Error getting course recommendations: {e}")
            return []
    
    def _get_recommendation_reason(self, course, major):
        """Generate reason for course recommendation"""
        reasons = []
        
        if course.department == major:
            reasons.append("Required for your major")
        
        if course.is_general_education:
            reasons.append("Fulfills general education requirement")
        
        level = int(course.code[-4:-3]) if course.code[-4:-3].isdigit() else 1
        if level <= 2:
            reasons.append("Foundation course")
        elif level >= 4:
            reasons.append("Advanced elective")
        
        return " | ".join(reasons) if reasons else "Recommended elective"
    
    def get_schedule_conflicts(self, user, course_code):
        """Check for schedule conflicts with current enrollment"""
        try:
            from accounts.models import CourseOffering, CourseEnrollment
            
            # Get the course offering
            offering = CourseOffering.objects.filter(
                course__code=course_code,
                semester='Spring 2025'
            ).first()
            
            if not offering:
                return False, "Course not offered this semester"
            
            # Get student's current schedule
            current_enrollments = CourseEnrollment.objects.filter(
                student=user,
                status__in=['enrolled', 'in_progress'],
                semester='Spring 2025'
            ).select_related('course')
            
            conflicts = []
            for enrollment in current_enrollments:
                # Check for time conflicts (simplified need actual schedule parsing)
                if self._schedules_conflict(offering.schedule, enrollment.schedule):
                    conflicts.append({
                        'course': enrollment.course.code,
                        'schedule': enrollment.schedule
                    })
            
            if conflicts:
                return True, conflicts
            
            return False, []
            
        except Exception as e:
            logger.error(f"Error checking schedule conflicts: {e}")
            return False, []
    
    def _schedules_conflict(self, schedule1, schedule2):
        """Check if two schedules conflict (simplified)"""
        # simplified version -need actual schedule parsing
        # For now, just return False
        return False


class ConversationMemoryManager:
    """Enhanced memory management for conversation persistence"""
    
    def __init__(self):
        self.conversation_cache = {}
        self.memory_cache = {}
        self.db_provider = DatabaseContextProvider()
        
    def get_or_create_session(self, user, session_id=None):
        """Get or create a session for the user with database context"""
        if not session_id:
            session_id = hashlib.md5(f"{user.id}_{datetime.now().isoformat()}".encode()).hexdigest()[:16]
        
        cache_key = f"{user.id}_{session_id}"
        if cache_key not in self.conversation_cache:
            # Initialize with database context
            student_data = self.db_provider.get_student_courses(user)
            profile_data = self.db_provider.get_student_profile(user)
            
            self.conversation_cache[cache_key] = {
                'messages': [],
                'context': {
                    'major': profile_data.get('major'),
                    'year': profile_data.get('year'),
                    'gpa': student_data.get('gpa'),
                    'total_credits': student_data.get('total_credits'),
                    'academic_standing': student_data.get('academic_standing')
                },
                'memories': [],
                'created_at': datetime.now().isoformat(),
                'last_active': datetime.now().isoformat()
            }
        
        self.conversation_cache[cache_key]['last_active'] = datetime.now().isoformat()
        return session_id, self.conversation_cache[cache_key]
    
    def add_to_conversation(self, user, session_id, role, content):
        """Add a message to the conversation history"""
        cache_key = f"{user.id}_{session_id}"
        if cache_key in self.conversation_cache:
            self.conversation_cache[cache_key]['messages'].append({
                'role': role,
                'content': content,
                'timestamp': datetime.now().isoformat()
            })
            if len(self.conversation_cache[cache_key]['messages']) > 50:
                self.conversation_cache[cache_key]['messages'] = self.conversation_cache[cache_key]['messages'][-50:]
    
    def extract_context_from_message(self, message_text):
        """Extract contextual information from message"""
        context = {}
        
        # Extract major
        major_patterns = [
            r"major(?:ing)? in ([A-Za-z\s]+?)(?:\.|,|;|$)",
            r"studying ([A-Za-z\s]+?)(?:\.|,|;|$)",
            r"my major is ([A-Za-z\s]+?)(?:\.|,|;|$)"
        ]
        for pattern in major_patterns:
            match = re.search(pattern, message_text, re.IGNORECASE)
            if match:
                context['major'] = match.group(1).strip()
                break
        
        # Extract course mentions
        course_pattern = r'\b([A-Z]{3,4})\s?(\d{3,4})\b'
        courses = re.findall(course_pattern, message_text)
        if courses:
            context['mentioned_courses'] = [f"{c[0]} {c[1]}" for c in courses]
        
        # Extract semester/year
        semester_pattern = r'(spring|summer|fall|winter)\s*(\d{4})'
        semester_match = re.search(semester_pattern, message_text, re.IGNORECASE)
        if semester_match:
            context['semester'] = f"{semester_match.group(1)} {semester_match.group(2)}"
        
        return context
    
    def clean_old_sessions(self, max_age_hours=24):
        """Clean up old sessions"""
        now = datetime.now()
        keys_to_delete = []
        
        for key, session in self.conversation_cache.items():
            last_active = datetime.fromisoformat(session['last_active'])
            if (now - last_active).total_seconds() > max_age_hours * 3600:
                keys_to_delete.append(key)
        
        for key in keys_to_delete:
            del self.conversation_cache[key]


# Global instances
memory_manager = ConversationMemoryManager()
db_provider = DatabaseContextProvider()


def safe_json_loads(s: str) -> Optional[Any]:
    """Tolerant JSON loader"""
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        try:
            start = s.index("{")
            end = s.rindex("}") + 1
            return json.loads(s[start:end])
        except Exception:
            return None


def call_openai_chat(messages: List[Dict[str, str]],
                    model: str = DEFAULT_MODEL,
                    temperature: float = DEFAULT_TEMPERATURE,
                    max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Call OpenAI API with messages"""
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI API key not configured.")

    formatted_messages = []
    for msg in messages:
        if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
            formatted_messages.append({
                'role': msg['role'],
                'content': str(msg['content'])
            })

    if _client is not None and hasattr(_client, "chat"):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=formatted_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.debug("Primary OpenAI client call failed: %s", e)

    raise RuntimeError("No OpenAI client available.")


def build_database_context_message(user, session_id=None) -> str:
    """Build comprehensive database context for the AI"""
    context_parts = []
    
    # Get all database context
    profile = db_provider.get_student_profile(user)
    courses = db_provider.get_student_courses(user)
    requirements = db_provider.get_degree_requirements(user)
    available = db_provider.get_available_courses(user, major=profile.get('major'))
    recommendations = db_provider.get_course_recommendations(user)
    
    # Get curriculum structure from Firebase
    curriculum_context = get_curriculum_context_for_ai(user)
    
    # Build context string
    context_parts.append("=== STUDENT PROFILE ===")
    context_parts.append(f"Name: {profile.get('name')}")
    context_parts.append(f"Major: {profile.get('major', 'Undeclared')}")
    context_parts.append(f"Year: {profile.get('year', 'Unknown')}")
    context_parts.append(f"GPA: {courses.get('gpa', 0.0)}")
    context_parts.append(f"Academic Standing: {courses.get('academic_standing', 'Unknown')}")
    context_parts.append(f"Total Credits Completed: {courses.get('total_credits', 0)}")
    context_parts.append(f"Credits In Progress: {courses.get('credits_in_progress', 0)}")
    
    context_parts.append("\n=== CURRENT ENROLLMENT ===")
    if courses.get('current_courses'):
        for course in courses['current_courses'][:5]:
            context_parts.append(f"- {course['code']}: {course['name']} ({course['credits']} credits)")
    else:
        context_parts.append("No courses currently enrolled")
    
    context_parts.append("\n=== COMPLETED COURSES (Recent) ===")
    if courses.get('completed_courses'):
        for course in courses['completed_courses'][:10]:
            context_parts.append(f"- {course['code']}: {course['name']} - Grade: {course['grade']}")
    else:
        context_parts.append("No completed courses")
    
    if courses.get('failed_courses'):
        context_parts.append("\n=== FAILED COURSES (Need to Retake) ===")
        for course in courses['failed_courses']:
            context_parts.append(f"- {course['code']}: {course['name']}")
    
    # Add curriculum structure context
    if curriculum_context:
        context_parts.append("\n" + curriculum_context)
    
    context_parts.append("\n=== DEGREE PROGRESS ===")
    context_parts.append(f"Major: {requirements.get('major')}")
    context_parts.append(f"Overall Progress: {requirements.get('overall_progress', 0)}%")
    context_parts.append(f"Credits: {requirements.get('total_credits_completed', 0)}/{requirements.get('total_credits_required', 120)}")
    context_parts.append(f"Estimated Graduation: {requirements.get('estimated_graduation', 'Unknown')}")
    
    context_parts.append("\n=== REQUIREMENT CATEGORIES ===")
    for req in requirements.get('requirements', [])[:5]:
        context_parts.append(f"- {req['category']}: {req['credits_completed']}/{req['credits_required']} credits ({req['progress_percentage']}%)")
        if req['remaining_courses']:
            context_parts.append(f"  Need: {', '.join([c['code'] for c in req['remaining_courses'][:3]])}")
    
    context_parts.append("\n=== RECOMMENDED COURSES ===")
    if recommendations:
        for rec in recommendations:
            context_parts.append(f"- {rec['code']}: {rec['name']} ({rec['credits']} cr)")
            context_parts.append(f"  Reason: {rec['reason']}")
    
    context_parts.append("\n=== AVAILABLE COURSES (Prerequisites Met) ===")
    available_filtered = [c for c in available if c.get('prerequisites_met', True)][:5]
    for course in available_filtered:
        context_parts.append(f"- {course['code']}: {course['name']} ({course['credits']} cr)")
        context_parts.append(f"  {course['schedule']} - {course['seats_available']} seats")
    
    return "\n".join(context_parts)


def get_curriculum_context_for_ai(user) -> str:
    """Get curriculum structure context for AI recommendations"""
    try:
        from firebase_service import firebase_service
        
        # Get curriculum metadata
        curriculum_metadata = firebase_service.get_curriculum_metadata()
        if not curriculum_metadata:
            return ""
        
        # Get all terms
        terms = firebase_service.get_all_terms()
        if not terms:
            return ""
        
        # Build context
        context_parts = ["=== CURRICULUM STRUCTURE (Computer Engineering 2025) ==="]
        
        # Add metadata
        grad_req = curriculum_metadata.get('graduation_requirements', {})
        context_parts.append(f"Total Credits Required: {grad_req.get('total_credits', 180)}")
        context_parts.append(f"Program Structure: {curriculum_metadata.get('curriculum_metadata', {}).get('total_terms', 13)} terms over {curriculum_metadata.get('curriculum_metadata', {}).get('academic_years', 4)} years")
        context_parts.append(f"Trimester System: Fall, Winter, Spring")
        
        context_parts.append("\n=== TERM PROGRESSION ===")
        
        # Sort terms by year and trimester
        def sort_key(term):
            term_info = term.get('term_info', {})
            year = term_info.get('year', 0)
            trimester_map = {'prep': 0, 'a': 1, 'b': 2, 'c': 3}
            trimester = trimester_map.get(term_info.get('trimester', 'a'), 1)
            return (year, trimester)
        
        sorted_terms = sorted(terms, key=sort_key)
        
        # Show first few terms with critical courses
        for term in sorted_terms[:8]:  # Limit to first 8 terms
            term_info = term.get('term_info', {})
            term_id = term.get('term_id', '')
            term_name = term_info.get('term_name', term_id)
            description = term_info.get('description', '')
            credits = term_info.get('total_credits_in_term', 0)
            critical_courses = term_info.get('critical_courses', [])
            recommended_trimester = term_info.get('recommended_trimester', [])
            
            context_parts.append(f"\n{term_name} ({', '.join(recommended_trimester) if recommended_trimester else 'Any trimester'}):")
            context_parts.append(f"  {description}")
            context_parts.append(f"  Total Credits: {credits}")
            
            if critical_courses:
                context_parts.append(f"  Critical Courses (required early): {', '.join(critical_courses[:3])}")
        
        context_parts.append("\n=== AI RECOMMENDATION GUIDANCE ===")
        ai_settings = curriculum_metadata.get('ai_recommendation_settings', {})
        if ai_settings:
            context_parts.append(f"- Prioritize critical courses that unlock later courses")
            context_parts.append(f"- Follow typical term progression for optimal path")
            context_parts.append(f"- Balance credits between {ai_settings.get('min_credits_per_trimester', 12)}-{ai_settings.get('max_credits_per_trimester', 16)} per trimester")
            context_parts.append(f"- Consider term availability (some courses only offered in specific trimesters)")
        
        return "\n".join(context_parts)
        
    except Exception as e:
        logger.error(f"Error getting curriculum context: {e}")
        return ""


# Enhanced link regex
_LINK_OR_URL_RE = re.compile(
    r'\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]+)\)|(?<!["\'>=/])\b(https?://[^\s<,.\)]+)', 
    flags=re.IGNORECASE
)


def reply_text_to_safe_html(text: str) -> str:
    """Convert assistant text to safe HTML with clickable links and better formatting"""
    if not text:
        return ""

    # First handle links before escaping to preserve markdown syntax
    link_placeholders = {}
    placeholder_counter = 0
    
    # Replace markdown links with placeholders
    def replace_link(match):
        nonlocal placeholder_counter
        placeholder = f"__LINK_PLACEHOLDER_{placeholder_counter}__"
        link_placeholders[placeholder] = match.group(0)
        placeholder_counter += 1
        return placeholder
    
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]+)\)', replace_link, text)
    
    # Now escape HTML to prevent injection
    text = escape(text)
    
    # Restore links and convert to HTML
    for placeholder, original in link_placeholders.items():
        match = re.match(r'\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]+)\)', original)
        if match:
            label = escape(match.group(1))
            url = escape(match.group(2), quote=True)
            if url.startswith('/'):
                html_link = f'<a href="{url}" style="color: #4a86e8; text-decoration: underline;">{label}</a>'
            else:
                html_link = f'<a href="{url}" target="_blank" rel="noopener noreferrer" style="color: #4a86e8; text-decoration: underline;">{label}</a>'
            text = text.replace(placeholder, html_link)
    
    # Convert markdown bold to HTML bold
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    
    # Convert markdown italic to HTML italic
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    
    # Process raw URLs
    text = re.sub(r'(?<!["\'>=/])\b(https?://[^\s<,.\)]+)', 
                r'<a href="\1" target="_blank" rel="noopener noreferrer" style="color: #4a86e8; text-decoration: underline;">\1</a>', 
                text)
    
    # Convert bullet points to nice HTML lists
    lines = text.split('\n')
    processed_lines = []
    in_list = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('• ') or stripped.startswith('- '):
            if not in_list:
                processed_lines.append('<ul style="margin: 5px 0; padding-left: 20px;">')
                in_list = True
            processed_lines.append(f'<li>{stripped[2:]}</li>')
        else:
            if in_list and not (stripped.startswith('• ') or stripped.startswith('- ')):
                processed_lines.append('</ul>')
                in_list = False
            if stripped:
                processed_lines.append(f'<p style="margin: 5px 0;">{line}</p>')
            else:
                processed_lines.append('<br>')
    
    if in_list:
        processed_lines.append('</ul>')
    
    html = ''.join(processed_lines)
    
    # Wrap the whole response in a nice container
    html = f'<div style="padding: 10px; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, \'Helvetica Neue\', Arial, sans-serif; line-height: 1.6; color: #333;">{html}</div>'
    
    return html


def detect_navigation_intent(text: str) -> Optional[tuple]:
    """Detect navigation intents from user message"""
    text_lower = text.lower().strip()
    
    navigation_intents = {
        "plan_next": {
            "keywords": ["plan next trimester", "plan next", "next trimester", "plan trimester", "take me to that tab"],
            "reply_text": "📚 **Navigating to Plan Next Trimester**\n\nClick here: [Plan Next Trimester](/accounts/plan-next-trimester/)\n\nThis is where you can plan your upcoming courses based on your degree requirements and prerequisites.",
            "reply_html": '<div style="padding: 10px; background: #f0f8ff; border-left: 4px solid #4a86e8; margin: 10px 0;">📚 <strong>Navigating to Plan Next Trimester</strong><br><br>Click here: <a href="/accounts/plan-next-trimester/" style="color: #4a86e8; font-weight: bold;">Plan Next Trimester</a><br><br><small>This is where you can plan your upcoming courses based on your degree requirements and prerequisites.</small></div>'
        },
        "edit_plans": {
            "keywords": ["edit trimester", "edit plans", "modify plans", "change plans"],
            "reply_text": "✏️ **Navigating to Edit Trimester Plans**\n\nClick here: [Edit Trimester Plans](/accounts/edit-trimester-plans/)\n\nHere you can modify your existing course plans and adjust your academic schedule.",
            "reply_html": '<div style="padding: 10px; background: #f0f8ff; border-left: 4px solid #4a86e8; margin: 10px 0;">✏️ <strong>Navigating to Edit Trimester Plans</strong><br><br>Click here: <a href="/accounts/edit-trimester-plans/" style="color: #4a86e8; font-weight: bold;">Edit Trimester Plans</a><br><br><small>Here you can modify your existing course plans and adjust your academic schedule.</small></div>'
        },
        "chatbot": {
            "keywords": ["academic chatbot", "planning chatbot", "ai assistant", "chatbot"],
            "reply_text": "💬 **Academic Planning Chatbot**\n\nYou're already here! I'm your AI assistant ready to help with course planning and academic advice.",
            "reply_html": '<div style="padding: 10px; background: #e8f5e9; border-left: 4px solid #4caf50; margin: 10px 0;">💬 <strong>Academic Planning Chatbot</strong><br><br>You\'re already here! I\'m your AI assistant ready to help with course planning and academic advice.</div>'
        },
        "update_progress": {
            "keywords": ["update progress", "progress update", "my progress", "academic progress"],
            "reply_text": "📈 **Navigating to Update Progress**\n\nClick here: [Update Progress](/accounts/update-progress/)\n\nTrack your degree completion, GPA, and overall academic progress.",
            "reply_html": '<div style="padding: 10px; background: #f0f8ff; border-left: 4px solid #4a86e8; margin: 10px 0;">📈 <strong>Navigating to Update Progress</strong><br><br>Click here: <a href="/accounts/update-progress/" style="color: #4a86e8; font-weight: bold;">Update Progress</a><br><br><small>Track your degree completion, GPA, and overall academic progress.</small></div>'
        },
        "dashboard": {
            "keywords": ["dashboard", "my dashboard", "student dashboard", "home"],
            "reply_text": "🏠 **Navigating to Dashboard**\n\nClick here: [Dashboard](/accounts/dashboard/)\n\nYour central hub for all academic information and tools.",
            "reply_html": '<div style="padding: 10px; background: #f0f8ff; border-left: 4px solid #4a86e8; margin: 10px 0;">🏠 <strong>Navigating to Dashboard</strong><br><br>Click here: <a href="/accounts/dashboard/" style="color: #4a86e8; font-weight: bold;">Dashboard</a><br><br><small>Your central hub for all academic information and tools.</small></div>'
        },
        "courses": {
            "keywords": ["course list", "browse courses", "course catalog", "all courses"],
            "reply_text": "📖 **Navigating to Course Catalog**\n\nClick here: [Course Catalog](/accounts/courses/)\n\nBrowse all available courses, check prerequisites, and view course descriptions.",
            "reply_html": '<div style="padding: 10px; background: #f0f8ff; border-left: 4px solid #4a86e8; margin: 10px 0;">📖 <strong>Navigating to Course Catalog</strong><br><br>Click here: <a href="/accounts/courses/" style="color: #4a86e8; font-weight: bold;">Course Catalog</a><br><br><small>Browse all available courses, check prerequisites, and view course descriptions.</small></div>'
        },
        "logout": {
            "keywords": ["logout", "log out", "sign out", "exit"],
            "reply_text": "👋 **Signing Out**\n\nTo log out, use the Log Out button in the navigation menu or go to your [Dashboard](/accounts/dashboard/) and click Log Out there.\n\nRemember to save any work before signing out!",
            "reply_html": '<div style="padding: 10px; background: #fff3e0; border-left: 4px solid #ff9800; margin: 10px 0;">👋 <strong>Signing Out</strong><br><br>To log out, use the Log Out button in the navigation menu or go to your <a href="/accounts/dashboard/" style="color: #ff9800; font-weight: bold;">Dashboard</a> and click Log Out there.<br><br><small>Remember to save any work before signing out!</small></div>'
        }
    }
    
    for intent_type, intent_data in navigation_intents.items():
        for keyword in intent_data["keywords"]:
            if keyword in text_lower:
                return (intent_type, intent_data["reply_text"], intent_data["reply_html"])
    
    return None


def ask_openai_companion(
    messages: Optional[List[Dict[str, str]]] = None,
    question: Optional[str] = None,
    user_context: Optional[str] = None,
    user=None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1500,
    temperature: float = DEFAULT_TEMPERATURE,
    current_url: Optional[str] = None,
    visible_menu: Optional[List[str]] = None,
    extra_context: Optional[str] = None,
    session_id: Optional[str] = None,
    include_memories: bool = True,
    memory_limit: int = 10,
    extract_to_memory: bool = True,
    origin_message_id: Optional[str] = None,
    return_html: bool = False,
) -> Any:
    """
    Enhanced PolyTech Companion with full database access
    Now provides real academic data and personalized recommendations
    """
    
    # Initialize session if user is provided
    session_context = {}
    database_context = ""
    
    if user:
        session_id, session_data = memory_manager.get_or_create_session(user, session_id)
        session_context = session_data.get('context', {})
        
        # Get comprehensive database context
        database_context = build_database_context_message(user, session_id)
    
    # Build base message list
    if question:
        system_prompt = (
            "You are PolyTech Companion, an AI assistant for Polytechnic University in San Juan, Puerto Rico.\n\n"
            
            "YOU HAVE ACCESS TO THE STUDENT'S REAL ACADEMIC DATA INCLUDING:\n"
            "- Their actual enrolled and completed courses with grades\n"
            "- Their GPA and academic standing\n"
            "- Their degree requirements and progress\n"
            "- Available courses they can register for\n"
            "- Prerequisites and course dependencies\n\n"
            
            "YOUR RESPONSIBILITIES:\n"
            "1. ACADEMIC ADVISOR: Provide specific, data-driven recommendations\n"
            "2. NAVIGATION ASSISTANT: Help navigate the website\n\n"
            
            "IMPORTANT: Use the real data provided below. Don't make up course codes or grades.\n"
            "Always reference actual courses from their record or available courses list.\n\n"
            
            "=== REAL STUDENT DATA ===\n"
            f"{database_context}\n"
            "=== END STUDENT DATA ===\n\n"
            
            "GUIDELINES FOR FORMATTING:\n"
            "- Use emojis appropriately (📚 for courses, ✅ for completed, ⚠️ for warnings, 🎯 for recommendations)\n"
            "- Use **bold** for important information\n"
            "- Break responses into clear sections with headers\n"
            "- Keep responses concise but informative\n"
            "- Use bullet points for lists\n"
            "- Give specific recommendations using real course codes\n"
            "- Reference their actual GPA and completed courses\n"
            "- Check prerequisites before recommending courses\n"
            "- Consider their degree progress and requirements\n"
            "- Use links: [text](URL) for navigation\n"
            
            "AVAILABLE NAVIGATION TABS:\n"
            "- Plan Next Trimester (/accounts/plan-next-trimester/)\n"
            "- Edit Trimester Plans (/accounts/edit-trimester-plans/)\n"
            "- Academic Planning Chatbot (current location)\n"
            "- Update Progress (/accounts/update-progress/)\n"
            "- Dashboard (/accounts/dashboard/)\n"
            "- Log Out (/accounts/logout/)\n"
        )
        
        if user_context:
            system_prompt += f"\nAdditional context: {user_context}"

        all_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ]

    elif messages:
        # Handle chat mode with database context
        last_message_text = messages[-1].get("content", "") if messages else ""
        
        # Extract context from message
        if user and last_message_text:
            new_context = memory_manager.extract_context_from_message(last_message_text)
            if new_context:
                session_context.update(new_context)
                if session_id:
                    cache_key = f"{user.id}_{session_id}"
                    if cache_key in memory_manager.conversation_cache:
                        memory_manager.conversation_cache[cache_key]['context'] = session_context
        
        # Check for navigation shortcuts
        nav_result = detect_navigation_intent(last_message_text)
        if nav_result:
            intent_type, reply_text, reply_html = nav_result
            
            if user and session_id:
                memory_manager.add_to_conversation(user, session_id, "user", last_message_text)
                memory_manager.add_to_conversation(user, session_id, "assistant", reply_text)
            
            if return_html:
                return reply_text, reply_html
            return reply_html  # Return HTML version by default for proper link rendering
        
        # Build system prompt with database context
        system_prompt = (
            "You are PolyTech Companion with access to real student data.\n\n"
            
            "=== LIVE STUDENT DATABASE ===\n"
            f"{database_context}\n"
            "=== END DATABASE ===\n\n"
            
            "CONVERSATION CONTEXT:\n"
            "- This is an ongoing conversation\n"
            "- Remember what was discussed earlier\n"
            "- Use the real data above for accurate advice\n"
            "- Reference specific courses by code\n"
            "- Check prerequisites before recommendations\n\n"
            
            "CAPABILITIES:\n"
            "- View their transcript and grades\n"
            "- Check degree requirements progress\n"
            "- See available courses with open seats\n"
            "- Verify prerequisites are met\n"
            "- Calculate GPA and credits\n\n"
            
            "FORMATTING GUIDELINES:\n"
            "- Use emojis for better visual appeal (📚 📖 ✅ ⚠️ 🎯 💡)\n"
            "- Use **bold** for emphasis on important points\n"
            "- Structure responses with clear sections\n"
            "- Keep responses friendly and helpful\n"
            "- Use bullet points for lists\n\n"
            
            "NAVIGATION HELP:\n"
            "If users ask to navigate, provide these links with nice formatting:\n"
            "- Plan Next Trimester: /accounts/plan-next-trimester/\n"
            "- Edit Trimester Plans: /accounts/edit-trimester-plans/\n"
            "- Update Progress: /accounts/update-progress/\n"
            "- Dashboard: /accounts/dashboard/\n"
            "- Log Out: /accounts/logout/\n\n"
            
            f"Current page: {current_url or 'Academic Planning Chatbot'}\n"
        )
        
        if session_context:
            system_prompt += f"\nSession context: {json.dumps(session_context, indent=2)}"
        
        system_message = {"role": "system", "content": system_prompt}
        all_messages = [system_message] + messages
        
    else:
        msg = ("🎓 **Welcome to PolyTech Companion!**\n\n"
            "I'm your AI academic assistant with access to your:\n"
            "• 📚 Course history and grades\n"
            "• 📊 GPA and academic standing\n"
            "• 🎯 Degree requirements\n"
            "• 📖 Available courses for registration\n\n"
            "**How can I help you today?**\n"
            "- Plan your next trimester\n"
            "- Check degree progress\n"
            "- Get course recommendations\n"
            "- Navigate to any section of the portal\n\n"
            "Just ask me anything about your academic journey!")
        if return_html:
            html_msg = ('<div style="padding: 15px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border-radius: 10px; margin: 10px 0;">'
                    '<h3 style="margin: 0 0 10px 0;">🎓 Welcome to PolyTech Companion!</h3>'
                    '<p>I\'m your AI academic assistant with access to your:</p>'
                    '<ul style="list-style: none; padding-left: 0;">'
                    '<li>📚 Course history and grades</li>'
                    '<li>📊 GPA and academic standing</li>'
                    '<li>🎯 Degree requirements</li>'
                    '<li>📖 Available courses for registration</li>'
                    '</ul>'
                    '<p><strong>How can I help you today?</strong></p>'
                    '<ul style="list-style: none; padding-left: 0;">'
                    '<li>• Plan your next trimester</li>'
                    '<li>• Check degree progress</li>'
                    '<li>• Get course recommendations</li>'
                    '<li>• Navigate to any section of the portal</li>'
                    '</ul>'
                    '<p style="margin-bottom: 0;">Just ask me anything about your academic journey!</p>'
                    '</div>')
            return msg, html_msg
        return reply_text_to_safe_html(msg)
    
    # Add memory context if available
    if include_memories and memory_service and user:
        try:
            from memory_service import build_memory_system_message
            mem_msg = build_memory_system_message(user, limit=memory_limit)
            if mem_msg:
                all_messages.insert(1, mem_msg)
        except Exception as e:
            logger.debug(f"Memory service not available: {e}")
    
    # Call OpenAI
    if not OPENAI_API_KEY or _client is None:
        fallback = "PolyTech Companion is unavailable. Visit your [Dashboard](/accounts/dashboard/)."
        fallback_html = reply_text_to_safe_html(fallback)
        if return_html:
            return fallback, fallback_html
        return fallback_html
    
    try:
        reply_text = call_openai_chat(all_messages, model=model, temperature=temperature, max_tokens=max_tokens)
    except Exception as e:
        logger.exception(f"OpenAI API error: {e}")
        fallback = "Connection error. Visit your [Dashboard](/accounts/dashboard/) or try again."
        fallback_html = reply_text_to_safe_html(fallback)
        if return_html:
            return fallback, fallback_html
        return fallback_html
    
    # Store conversation
    if user and session_id and messages:
        memory_manager.add_to_conversation(user, session_id, "user", messages[-1].get("content", ""))
        memory_manager.add_to_conversation(user, session_id, "assistant", reply_text)
    
    # Extract memories
    if extract_to_memory and memory_service and user:
        try:
            last_user_text = None
            for m in reversed(all_messages):
                if m.get("role") == "user":
                    last_user_text = m.get("content", "")
                    break
            if last_user_text:
                memory_service.extract_and_store_memories_from_message(
                    user, last_user_text, origin_message_id=origin_message_id
                )
        except Exception as e:
            logger.debug(f"Memory extraction failed: {e}")
    
    # Clean old sessions
    memory_manager.clean_old_sessions(max_age_hours=48)
    
    if return_html:
        return reply_text, reply_text_to_safe_html(reply_text)
    # Return plain text by default
    return reply_text


# Backwards compatibility
ask_openai_chatbot = ask_openai_companion
ask_openai_advisor = ask_openai_companion