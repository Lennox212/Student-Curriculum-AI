from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from .firebase_auth_service import firebase_auth_service
from .forms import UserRegistrationForm, CurriculumCreateForm, TermCreateForm, CourseCreateForm
from django.contrib import messages
from django.http import JsonResponse
import json
from django.http import JsonResponse
import logging
import uuid
import os
from firebase_service import firebase_service
from firebase_admin import firestore
import openai
from .openai_service import ask_openai_chatbot, ask_openai_advisor
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse

logger = logging.getLogger(__name__)


@login_required
def structured_ai_suggestion(request):
    """
    AI-powered endpoint that analyzes student's academic progress and suggests
    an optimal trimester plan considering:
    - Prerequisites and course dependencies
    - Workload balance and difficulty
    - Bottleneck courses that unlock future courses
    - Path to graduation
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        # Get user's selected concentration
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        data = json.loads(request.body)
        completed_courses = data.get('completed_courses', [])
        available_courses = data.get('available_courses', [])
        current_selection = data.get('current_selection', [])

        # Build context for AI
        ai_context = build_ai_context(
            completed_courses,
            available_courses,
            current_selection,
            curriculum_code
        )

        # Call OpenAI to generate suggestion
        suggestion = generate_ai_suggestion(ai_context)

        return JsonResponse({
            'success': True,
            'suggestion': suggestion
        })

    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


def build_ai_context(completed_courses, available_courses, current_selection, curriculum_code='Comp_Eng_2025'):
    """
    Builds structured context for AI to analyze with term metadata
    """
    # Handle both formats: list of strings or list of dicts
    if completed_courses and isinstance(completed_courses[0], str):
        # If completed_courses is a list of course codes (strings)
        total_credits_completed = len(completed_courses) * 3  # Estimate
        completed_courses_data = completed_courses  # Keep as-is for AI
    else:
        # If completed_courses is a list of dicts
        total_credits_completed = sum(c.get('credits', 3) for c in completed_courses)
        completed_courses_data = [
            {
                'code': c.get('course_code') if isinstance(c, dict) else c,
                'name': c.get('course_name', '') if isinstance(c, dict) else ''
            }
            for c in completed_courses
        ]

    # Identify bottleneck courses (courses that unlock many others)
    bottleneck_courses = identify_bottlenecks(available_courses)

    # Categorize courses by component and term
    courses_by_component = {}
    courses_by_term = {}
    critical_courses = []

    for course in available_courses:
        component = course.get('component', 'Other')
        if component not in courses_by_component:
            courses_by_component[component] = []
        courses_by_component[component].append(course)

        # Get term info if available
        term_info = course.get('term_info', {})
        term_id = course.get('term_id', 'unknown')

        if term_id not in courses_by_term:
            courses_by_term[term_id] = {
                'courses': [],
                'term_info': term_info
            }
        courses_by_term[term_id]['courses'].append(course)

        # Track critical courses
        if term_info.get('critical_courses') and course.get('code') in term_info.get('critical_courses', []):
            critical_courses.append(course)

    # Get curriculum metadata for better context
    curriculum_metadata = firebase_service.get_curriculum_metadata(curriculum_code=curriculum_code)
    graduation_requirements = curriculum_metadata.get('graduation_requirements', {})

    context = {
        'completed_courses_count': len(completed_courses),
        'completed_courses': completed_courses_data,
        'total_credits_completed': total_credits_completed,
        'available_courses': available_courses,
        'bottleneck_courses': bottleneck_courses,
        'critical_courses': critical_courses,
        'courses_by_component': courses_by_component,
        'courses_by_term': courses_by_term,
        'current_selection': current_selection,
        'recommended_credit_range': (12, 16),
        'graduation_requirements': graduation_requirements,
        'typical_graduation_credits': graduation_requirements.get('total_credits', 180)
    }

    return context


def identify_bottlenecks(available_courses):
    """
    Identifies courses that are prerequisites for many other courses
    These are "bottleneck" courses that should be prioritized
    """
    # Build a prerequisite graph
    prerequisite_count = {}

    for course in available_courses:
        code = course.get('course_code')
        # Count how many other courses require this as prerequisite
        # This would need your actual course data structure
        count = 0
        for other_course in available_courses:
            prereqs = other_course.get('prerequisites', [])
            if code in prereqs:
                count += 1

        if count > 0:
            prerequisite_count[code] = count

    # Return courses that unlock 2+ other courses
    bottlenecks = [code for code, count in prerequisite_count.items() if count >= 2]
    return bottlenecks


def generate_ai_suggestion(context):
    """
    Uses OpenAI to generate intelligent course suggestions
    """
    # Calculate actual progress
    completed_count = context['completed_courses_count']
    total_credits = context['total_credits_completed']

    # Determine student level
    if completed_count < 10:
        student_level = "beginner (first year)"
    elif completed_count < 25:
        student_level = "intermediate (second year)"
    elif completed_count < 40:
        student_level = "advanced (third year)"
    else:
        student_level = "senior (final year)"

    # Build detailed course list with prerequisites
    available_courses_details = []
    for course in context['available_courses']:
        course_info = {
            'code': course.get('course_code'),
            'name': course.get('course_name'),
            'credits': course.get('credits'),
            'component': course.get('component'),
            'prerequisites': course.get('prerequisites', [])
        }
        available_courses_details.append(course_info)

    # Extract term progression context
    term_context = ""
    if context.get('courses_by_term'):
        terms_summary = []
        for term_id, term_data in context['courses_by_term'].items():
            term_info = term_data.get('term_info', {})
            if term_info:
                terms_summary.append(
                    f"Term {term_id}: {term_info.get('term_name', '')} - {term_info.get('description', '')}")
        if terms_summary:
            term_context = "\n\nTerm Progression:\n" + "\n".join(terms_summary[:5])

    critical_courses_context = ""
    if context.get('critical_courses'):
        critical_list = [f"{c.get('code', '')}: {c.get('title', '')}" for c in context['critical_courses'][:5]]
        critical_courses_context = "\n\nCritical Courses (foundational, unlock many others):\n" + "\n".join(
            critical_list)

    # Prepare the prompt for AI
    prompt = f"""
    You are an academic advisor AI for university students in a trimester-based Computer Engineering program.

    CRITICAL RULES - YOU MUST FOLLOW THESE:
    1. NEVER suggest a course if its prerequisites are not completed
    2. ALWAYS consider LOGICAL prerequisites, even if not formally listed. Here are some generic logical examples but not limited to:
    - Advanced courses require foundational courses in the same subject
    - "Software Engineering" requires basic programming courses
    - "Data Structures" requires intro programming
    - "Calculus II" requires "Calculus I"
    - "Physics II" requires "Physics I"
    - Advanced topics require intermediate topics in the same field
    3. For beginner students (< 10 courses), suggest ONLY foundational/preparatory courses
    4. Balance the workload between 12-16 credits (4-5 courses)
    5. PRIORITIZE critical courses that unlock future courses and are required early in the program{term_context}{critical_courses_context}
    5. Prioritize courses that unlock many future courses (bottlenecks)

    STUDENT'S CURRENT STATUS:
    - Completed: {completed_count} courses ({total_credits} credits)
    - Student Level: {student_level}
    - Progress toward graduation: {(total_credits / context['typical_graduation_credits'] * 100):.1f}%

    COMPLETED COURSES (Student has already taken these):
    {json.dumps(context.get('completed_courses', []), indent=2) if context.get('completed_courses') else "No courses completed yet - this is a NEW student!"}

    AVAILABLE COURSES (Student CAN take - technical prerequisites verified, but CHECK LOGICAL prerequisites):
    {json.dumps(available_courses_details, indent=2)}

    BOTTLENECK COURSES (unlock many future courses - HIGH PRIORITY):
    {', '.join(context['bottleneck_courses']) if context['bottleneck_courses'] else 'None identified'}

    INSTRUCTIONS FOR COURSE SELECTION:

    1. **Analyze Course Names for Logical Prerequisites:**
    Before suggesting ANY course, examine its name:

    - If it contains "Software", "Advanced", "Systems", "Design", "Engineering":
        → Student MUST have completed basic programming/intro courses first

    - If it contains "II", "2", "Advanced", "Intermediate":
        → Student MUST have completed the level I or introductory version

    - If it contains "Algorithms", "Data Structures", "Operating Systems":
        → Student MUST have programming fundamentals completed

    - If it's a 3000+ or 4000+ level course:
        → Student should have completed 1000-2000 level courses in that subject

    - If it's "Preparatory" or "Introduction" or "Fundamentals":
        → Safe for beginners (< 10 courses completed)

    2. **Check Student Level:**
    - If beginner (< 10 courses): Suggest ONLY "Preparatory", "Introduction", "Fundamentals", or 0000-1000 level courses
    - If intermediate (10-25 courses): Core 2000-3000 level courses
    - If advanced (25-40 courses): 3000-4000 level specialized courses
    - If senior (40+ courses): Remaining requirements and capstone

    3. **Verify Logical Course Sequences:**
    Common sequences you MUST respect:
    - Mathematics: Preparatory Math → Calculus I → Calculus II → Differential Equations
    - Programming: Intro/Fundamentals → Data Structures → Algorithms → Software Engineering/Systems
    - Physics/Science: Intro Physics → Physics I → Physics II
    - Language/Communication: Preparatory → Intermediate → Advanced

    **NEVER suggest an advanced course if the foundational course isn't completed!**

    4. **Trimester-Based Planning:**
    - The program follows a Fall/Winter/Spring trimester system
    - Some courses are only offered in specific trimesters
    - Consider term_availability when suggesting courses
    - Critical courses (marked as such in term_info) should be prioritized as they're often prerequisites for multiple later courses
    - Follow the typical_next_terms guidance when available to suggest logical progression

    5. **Course Selection Strategy:**
    - Select 4-5 courses totaling 12-16 credits
    - Mix of difficult and easier courses for balance
    - Include at least one critical/bottleneck course if appropriate for student level
    - Spread across different subjects (not all math or all programming)
    - Prioritize foundational courses for beginners
    - Consider component distribution (LEC, LAB, etc.) for balanced workload

    6. **Reasoning Quality:**
    - Explain WHY this course is appropriate for THIS student's current level
    - If suggesting an advanced course, explicitly state which prerequisite the student has completed
    - Mention if it's foundational and should be taken early
    - Note if it unlocks important future courses

    **CRITICAL: Do NOT just copy example courses! Select from AVAILABLE COURSES based on student's actual progress.**

    Return ONLY valid JSON in this exact format:
    {{
    "suggested_courses": [
        {{
        "course_code": "COURSE_CODE_FROM_AVAILABLE_LIST",
        "course_name": "Exact Course Name From Available List",
        "credits": 3,
        "reasoning": "Detailed explanation of why THIS course is appropriate for a student with {completed_count} completed courses. If advanced, state which prerequisite was completed.",
        "priority": "high",
        "is_bottleneck": true,
        "difficulty": "balanced"
        }}
    ],
    "analysis": {{
        "total_credits": 15,
        "difficulty": "Balanced",
        "courses_unlocked": 8,
        "graduation_progress": 15
    }}
    }}

    REMEMBER: 
    - For NEW students (0-10 courses): ONLY suggest preparatory/intro/fundamentals courses!
    - NEVER suggest "Software Engineering" without programming courses completed!
    - NEVER suggest "Calculus II" without "Calculus I" completed!
    - NEVER suggest advanced courses to beginners!
    - Look at course names and numbers to infer logical prerequisites!
    """

    try:
        # Call OpenAI API
        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)

        response = client.chat.completions.create(
            model="gpt-4o",  # Use GPT-4 for better reasoning
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert academic advisor with deep knowledge of Computer Engineering curriculum sequencing. You understand logical course prerequisites and progression. Always respond with valid JSON only. You prioritize student success by recommending appropriate courses for their level."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # Lower temperature for more consistent, logical responses
            max_tokens=2500
        )

        # Parse AI response
        ai_response = response.choices[0].message.content.strip()

        # Remove markdown code blocks if present
        if ai_response.startswith('```json'):
            ai_response = ai_response[7:]
        if ai_response.startswith('```'):
            ai_response = ai_response[3:]
        if ai_response.endswith('```'):
            ai_response = ai_response[:-3]

        suggestion = json.loads(ai_response.strip())

        valid_codes = set()
        for course in context['available_courses']:
            code = course.get('course_code', '')
            valid_codes.add(code)  # Original format
            valid_codes.add(code.replace(' ', '_'))  # With underscore
            valid_codes.add(code.replace('_', ' '))  # With space
            valid_codes.add(code.replace(' ', '').replace('_', ''))  # No separator

        # Filter suggested courses - keep if ANY format matches
        filtered_suggestions = []
        for suggested_course in suggestion['suggested_courses']:
            ai_code = suggested_course['course_code']

            # Check if AI's code matches any valid format
            if (ai_code in valid_codes or
                    ai_code.replace(' ', '_') in valid_codes or
                    ai_code.replace('_', ' ') in valid_codes or
                    ai_code.replace(' ', '').replace('_', '') in valid_codes):
                filtered_suggestions.append(suggested_course)
            else:
                # Try to find matching course by name
                ai_name = suggested_course['course_name'].lower()
                for course in context['available_courses']:
                    if course.get('course_name', '').lower() == ai_name:
                        # Update the course code to match the actual format
                        suggested_course['course_code'] = course.get('course_code')
                        filtered_suggestions.append(suggested_course)
                        break

        suggestion['suggested_courses'] = filtered_suggestions

        # Recalculate credits after filtering
        suggestion['analysis']['total_credits'] = sum(
            c['credits'] for c in suggestion['suggested_courses']
        )

        return suggestion

    except json.JSONDecodeError as e:
        print(f"JSON Parse Error: {e}")
        print(f"AI Response was: {ai_response}")
        return generate_fallback_suggestion(context)
    except Exception as e:
        print(f"Error calling OpenAI: {e}")
        return generate_fallback_suggestion(context)


def generate_fallback_suggestion(context):
    """
    Rule-based fallback if AI fails
    Provides a reasonable suggestion based on simple rules
    """
    available = context['available_courses']
    bottlenecks = context['bottleneck_courses']

    # Simple heuristic: prioritize bottlenecks, then required courses
    suggested = []
    total_credits = 0

    # First, add bottleneck courses
    for course in available:
        if course['course_code'] in bottlenecks and total_credits + course['credits'] <= 16:
            suggested.append({
                'course_code': course['course_code'],
                'course_name': course['course_name'],
                'credits': course['credits'],
                'reasoning': 'This course unlocks multiple future courses and should be prioritized.',
                'priority': 'high',
                'is_bottleneck': True,
                'difficulty': 'balanced'
            })
            total_credits += course['credits']

    # Then add other required courses
    for course in available:
        if course['course_code'] not in bottlenecks and total_credits + course['credits'] <= 16:
            if len(suggested) < 5:
                suggested.append({
                    'course_code': course['course_code'],
                    'course_name': course['course_name'],
                    'credits': course['credits'],
                    'reasoning': 'This course contributes to your graduation requirements.',
                    'priority': 'medium',
                    'is_bottleneck': False,
                    'difficulty': 'balanced'
                })
                total_credits += course['credits']

    return {
        'suggested_courses': suggested,
        'analysis': {
            'total_credits': total_credits,
            'difficulty': 'Balanced',
            'difficulty_level': 'moderate',
            'credit_level': 'good' if 12 <= total_credits <= 16 else 'moderate',
            'courses_unlocked': len(bottlenecks),
            'graduation_progress': round(
                (context['total_credits_completed'] + total_credits) / context['typical_graduation_credits'] * 100, 1)
        }
    }


def home_view(request):
    if request.user.is_authenticated:
        if not getattr(request.user, "selected_concentration", None):
            return redirect('concentration_selection')
        elif not getattr(request.user, "has_completed_setup", None):
            return redirect('course_checklist')
        else:
            return redirect('dashboard')
    return render(request, 'accounts/home.html')


def register(request):
    if request.method == 'POST':
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
                form = UserRegistrationForm(data)
                if form.is_valid():
                    new_user = form.save(commit=False)
                    new_user.email = form.cleaned_data['email']
                    new_user.set_password(form.cleaned_data['password'])
                    new_user.save()

                    # ✨ NEW: Create Firebase user
                    firebase_user = firebase_auth_service.create_or_update_firebase_user(new_user)

                    # ✨ NEW: Generate Firebase custom token
                    custom_token = firebase_auth_service.create_custom_token(
                        uid=str(new_user.id),
                        additional_claims={'username': new_user.username}
                    )

                    login(request, new_user)

                    return JsonResponse({
                        'success': True,
                        'message': 'Registration successful. You are now logged in.',
                        'redirect_url': '/accounts/concentration/',
                        'firebase_token': custom_token  # ✨ NEW: Send token to frontend
                    })
                else:
                    field_errors = {field: [str(error) for error in errors] for field, errors in form.errors.items()}
                    return JsonResponse({'success': False, 'field_errors': field_errors}, status=400)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'errors': ['Invalid JSON data']}, status=400)
        else:
            form = UserRegistrationForm(request.POST)
            if form.is_valid():
                new_user = form.save(commit=False)
                new_user.email = form.cleaned_data['email']
                new_user.set_password(form.cleaned_data['password'])
                new_user.save()

                # ✨ NEW: Create Firebase user
                firebase_auth_service.create_or_update_firebase_user(new_user)

                messages.success(request, 'Registration successful. You are now logged in.')
                login(request, new_user)
                return redirect('concentration_selection')
    else:
        form = UserRegistrationForm()
    return render(request, 'accounts/register.html', {'form': form})


def custom_login(request):
    if request.method == 'POST':
        if request.content_type == 'application/json':
            try:
                data = json.loads(request.body)
                username = data.get('username')
                password = data.get('password')
                if not username or not password:
                    return JsonResponse({'success': False, 'errors': ['Username and password are required.']},
                                        status=400)
                user = authenticate(request, username=username, password=password)
                if user is not None:
                    # ✨ NEW: Ensure Firebase user exists
                    firebase_auth_service.create_or_update_firebase_user(user)

                    # ✨ NEW: Generate Firebase custom token
                    custom_token = firebase_auth_service.create_custom_token(
                        uid=str(user.id),
                        additional_claims={'username': user.username}
                    )

                    login(request, user)

                    if not getattr(user, "selected_concentration", None):
                        redirect_url = '/accounts/concentration/'
                    elif not getattr(user, "has_completed_setup", None):
                        redirect_url = '/accounts/course-checklist/'
                    else:
                        redirect_url = '/accounts/dashboard/'

                    return JsonResponse({
                        'success': True,
                        'message': 'Login successful!',
                        'redirect_url': redirect_url,
                        'firebase_token': custom_token  # ✨ NEW: Send token to frontend
                    })
                else:
                    return JsonResponse({'success': False, 'errors': ['Invalid username or password.']}, status=400)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'errors': ['Invalid JSON data']}, status=400)
        else:
            username = request.POST.get('username')
            password = request.POST.get('password')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                # ✨ NEW: Ensure Firebase user exists
                firebase_auth_service.create_or_update_firebase_user(user)

                login(request, user)
                if not getattr(user, "selected_concentration", None):
                    return redirect('concentration_selection')
                elif not getattr(user, "has_completed_setup", None):
                    return redirect('course_checklist')
                else:
                    return redirect('dashboard')
            else:
                messages.error(request, 'Invalid username or password.')
    return render(request, 'accounts/login.html')


# Utility to load courses JSON
def load_courses():
    # Points directly to the project root
    json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'computer_engineering_courses.json')
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Cannot find courses JSON file at {json_path}")
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


@login_required
# def concentration_selection(request):
#     return render(request, 'accounts/concentration_selection.html')

@login_required
def concentration_selection(request):
    """
    Display concentration selection page with dynamic data from Firebase.
    Fetches active concentrations that were created in Django Admin and synced to Firebase.
    """
    try:
        concentrations_data = []

        # Query Firebase for active concentrations
        if firebase_service.db:
            concentrations_ref = firebase_service.db.collection('concentrations')
            # Get only active concentrations, ordered by year (newest first)
            query = concentrations_ref.where('is_active', '==', True).order_by('year',
                                                                               direction=firestore.Query.DESCENDING)
            docs = query.stream()

            for doc in docs:
                concentration = doc.to_dict()
                concentrations_data.append({
                    'id': concentration.get('code', ''),  # e.g., "comp_eng_2024"
                    'name': concentration.get('name', ''),  # e.g., "Computer Engineering"
                    'description': f"{concentration.get('total_credits_required', 0)} credits - {concentration.get('description', '')}",
                    'year': concentration.get('year', ''),  # e.g., 2024
                    'available': True  # All active concentrations are available to select
                })

            logger.info(f"Loaded {len(concentrations_data)} concentrations from Firebase")

        # Fallback if Firebase is not available
        if not concentrations_data:
            logger.warning("No concentrations found in Firebase or Firebase unavailable, using fallback")
            concentrations_data = [
                {
                    'id': 'COMPUTER_ENGINEERING',
                    'name': 'Computer Engineering',
                    'description': '182 credits - Bachelor of Science in Computer Engineering',
                    'available': True
                }
            ]

    except Exception as e:
        logger.error(f"Error loading concentrations: {e}", exc_info=True)
        # Fallback on any error
        concentrations_data = [
            {
                'id': 'COMPUTER_ENGINEERING',
                'name': 'Computer Engineering',
                'description': '182 credits - Bachelor of Science in Computer Engineering',
                'available': True
            }
        ]

    # Pass concentrations to template as JSON
    return render(request, 'accounts/concentration_selection.html', {
        'concentrations': json.dumps(concentrations_data)  # Convert to JSON string for Vue
    })


@login_required
def save_concentration(request):
    if request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            concentration = data.get('concentration')

            if not concentration:
                return JsonResponse({'success': False, 'errors': ['No concentration provided']}, status=400)

            # Validate that the concentration exists in Firebase
            curricula = firebase_service.get_all_curricula()
            valid_codes = [c.get('code', '') for c in curricula]

            if concentration not in valid_codes:
                logger.warning(f"Invalid concentration attempted: {concentration}. Valid: {valid_codes}")
                return JsonResponse({'success': False, 'errors': ['Invalid concentration selected']}, status=400)

            # Save the concentration
            request.user.selected_concentration = concentration
            request.user.save()
            logger.info(f"User {request.user.username} selected concentration: {concentration}")

            return JsonResponse({'success': True, 'message': 'Concentration saved successfully'})

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'errors': ['Invalid JSON data']}, status=400)
        except Exception as e:
            logger.error(f"Error saving concentration: {e}")
            return JsonResponse({'success': False, 'errors': [str(e)]}, status=500)

    return JsonResponse({'success': False, 'errors': ['Invalid request']}, status=400)


@login_required
def course_checklist(request):
    # Normalize old completed courses format (remove underscores)
    user_completed_courses = getattr(request.user, "completed_courses_json", []) or []
    normalized_completed = [course_id.replace('_', '') for course_id in
                            user_completed_courses] if user_completed_courses else []

    # Update user's completed courses if they were changed
    if normalized_completed != user_completed_courses:
        request.user.completed_courses_json = normalized_completed
        request.user.save()

    # Log for debugging
    logger.info(f"Course checklist - User: {request.user.username}, Completed: {normalized_completed}")

    return render(request, 'accounts/course_checklist.html', {
        'completed_courses': normalized_completed,
    })


@login_required
def api_curriculum_courses(request):
    try:
        # Get the user's selected concentration (curriculum code)
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        # Fetch courses for the user's specific curriculum
        courses = firebase_service.get_curriculum_courses(curriculum_code=curriculum_code)

        # Log for debugging
        logger.info(f"api_curriculum_courses: User {request.user.username} - Curriculum: {curriculum_code}")
        logger.info(f"api_curriculum_courses: Loaded {len(courses)} courses")
        total_credits = sum(c.get('credits', 0) for c in courses)
        logger.info(f"api_curriculum_courses: Total credits = {total_credits}")

        courses_data = []
        seen_codes = set()
        duplicates = []

        for course in courses:
            # Use the course code as ID (already set by firebase_service)
            # This ensures consistency with completed_courses tracking
            course_code = course.get('code', '')

            # Track duplicates
            if course_code in seen_codes:
                duplicates.append(course_code)
                logger.warning(f"Duplicate course found: {course_code}")
                continue
            seen_codes.add(course_code)

            course_info = {
                'id': course.get('id'),  # Already set to course code in firebase_service
                'course_code': course_code,
                'course_name': course.get('title', course.get('description', '')),
                'credits': course.get('credits', 0),
                'prerequisites': course.get('prerequisites', []),
                'corequisites': course.get('corequisites', []),
                'component': course.get('component', ''),
                'term_availability': course.get('term', []),
                'term_id': course.get('term_id', ''),
                'term_info': course.get('term_info', {})
            }
            courses_data.append(course_info)

        if duplicates:
            logger.warning(f"Found {len(duplicates)} duplicate courses: {duplicates}")

        final_total = sum(c['credits'] for c in courses_data)
        logger.info(f"api_curriculum_courses: After deduplication = {len(courses_data)} courses, {final_total} credits")

        # Get the curriculum title for display
        concentration_title = curriculum_code.replace('_', ' ').replace('Comp Eng', 'Computer Engineering').replace(
            'Comp Sci', 'Computer Science')

        return JsonResponse({
            'courses': courses_data,
            'concentration': concentration_title,
            'total_courses': len(courses_data),
            'total_credits': final_total  # Send calculated total to frontend
        })
    except Exception as e:
        logger.error(f"Error loading curriculum courses: {e}")
        return JsonResponse({'error': 'Failed to load curriculum from Firebase'}, status=500)


@login_required
def api_available_concentrations(request):
    """
    API endpoint to fetch all available concentrations/curricula from Firestore
    """
    try:
        curricula = firebase_service.get_all_curricula()

        # Transform curriculum data into concentration format
        concentrations = []
        for curriculum in curricula:
            # Get concentration info (contains title)
            concentration_info = curriculum.get('concentration', {})
            curriculum_metadata = curriculum.get('curriculum_metadata', {})

            # Extract total credits from graduation requirements
            graduation_reqs = curriculum.get('graduation_requirements', {})
            total_credits = graduation_reqs.get('total_credits', 0)

            concentration = {
                'id': curriculum.get('code', ''),
                'name': concentration_info.get('title', 'Unknown'),
                'description': f"{total_credits} credits",
                'available': True,  # All curricula in Firestore are available
                'credits': total_credits,
                'version': concentration_info.get('version', ''),
                'campus': concentration_info.get('campus', ''),
            }
            concentrations.append(concentration)

        logger.info(f"api_available_concentrations: Loaded {len(concentrations)} concentrations")

        return JsonResponse({
            'concentrations': concentrations,
            'success': True
        })
    except Exception as e:
        logger.error(f"Error loading concentrations: {e}")
        return JsonResponse({'error': 'Failed to load concentrations from Firebase', 'success': False}, status=500)


@login_required
def save_course_progress(request):
    if request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            completed_course_ids = data.get('completed_courses', [])

            # Normalize course IDs by removing underscores for consistency
            # This handles migration from old format (MATH_1350) to new format (MATH1350)
            normalized_ids = [course_id.replace('_', '') for course_id in completed_course_ids]

            request.user.completed_courses_json = normalized_ids
            request.user.has_completed_setup = True
            request.user.save()
            return JsonResponse({
                'success': True,
                'message': 'Course progress saved successfully',
                'completed_courses': len(normalized_ids)
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'errors': ['Invalid JSON data']}, status=400)
        except Exception as e:
            logger.error(f"Error saving course progress: {e}")
            return JsonResponse({'success': False, 'errors': ['Server error occurred']}, status=500)
    return JsonResponse({'success': False, 'errors': ['Invalid request']}, status=400)


@login_required
def dashboard(request):
    try:
        # Get user's selected concentration
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        # Get completed courses and normalize format (remove underscores)
        completed_courses_ids = getattr(request.user, "completed_courses_json", [])
        if not completed_courses_ids:
            completed_courses_ids = []

        # Normalize by removing underscores from old format
        normalized_completed = [course_id.replace('_', '') for course_id in completed_courses_ids]

        # Get all valid courses from the user's specific curriculum
        all_courses = firebase_service.get_curriculum_courses(curriculum_code=curriculum_code)
        valid_course_ids = {course['id'] for course in all_courses}

        # Calculate actual total credits from curriculum
        total_credits = int(sum(course.get('credits', 0) for course in all_courses))

        # Filter out any courses that don't exist in the curriculum (e.g., old electives)
        valid_completed = [course_id for course_id in normalized_completed if course_id in valid_course_ids]

        # Update database if we filtered out invalid courses or normalized anything
        if valid_completed != completed_courses_ids:
            request.user.completed_courses_json = valid_completed
            request.user.save()

        completed_courses_ids = valid_completed

        course_credits_map = {course['id']: course['credits'] for course in all_courses}
        completed_credits = sum(int(course_credits_map.get(course_id, 0)) for course_id in completed_courses_ids)
        progress_percentage = round((completed_credits / total_credits) * 100) if total_credits > 0 else 0

        # Log for debugging
        logger.info(
            f"Dashboard - User: {request.user.username}, Curriculum: {curriculum_code}, Completed IDs: {completed_courses_ids}, Count: {len(completed_courses_ids)}, Credits: {completed_credits}/{total_credits}")

        # Get concentration title from Firebase
        concentration_title = "Computer Engineering"  # Default fallback
        try:
            curriculum_metadata = firebase_service.get_curriculum_metadata(curriculum_code=curriculum_code)
            concentration_info = curriculum_metadata.get('concentration', {})
            concentration_title = concentration_info.get('title', concentration_title)
            logger.info(f"Dashboard - Concentration title: {concentration_title}")
        except Exception as e:
            logger.warning(f"Could not fetch concentration title, using fallback: {e}")
            # Fallback: clean up the code for display
            concentration_title = curriculum_code.replace('_', ' ')

        context = {
            'completed_courses_count': len(completed_courses_ids),
            'total_credits': total_credits,
            'completed_credits': completed_credits,
            'progress_percentage': progress_percentage,
            'concentration': concentration_title,
            'current_semester': 1,
            'saved_card_style': getattr(request.user, 'dashboard_card_style', '')
        }
        return render(request, 'accounts/dashboard.html', context)
    except Exception as e:
        logger.error(f"Error loading dashboard: {e}")
        context = {
            'error': 'Could not load progress data',
            'concentration': getattr(request.user, "selected_concentration", 'Computer Engineering')
        }
        return render(request, 'accounts/dashboard.html', context)


@login_required
def view_curriculum_plan(request):
    try:
        courses_raw = load_courses()

        # Helper: canonicalize course codes
        def canonical_code(code):
            if not code:
                return ''
            return str(code).replace(' ', '').replace('_', '').upper()  # remove spaces, uppercase

        # Completed courses from user profile
        completed_raw = getattr(request.user, "completed_courses_json", []) or []
        completed_set = set([canonical_code(c) for c in completed_raw])

        # Planned courses from user profile
        planned_raw = request.user.get_planned_courses()
        planned_set = set([canonical_code(c) for c in planned_raw])

        courses = []
        for c in courses_raw:
            # Prefer 'code' or 'course_code' from JSON
            course_code = c.get('code') or c.get('course_code') or ''
            cid = canonical_code(course_code)

            desc = c.get('description') or c.get('course_name') or ''
            credits = c.get('credits') or 0
            try:
                credits = float(credits)
            except:
                credits = 0

            # Determine status with priority: Completed > Planned > Pending
            if cid in completed_set:
                status = "Completed"
            elif cid in planned_set:
                status = "Planned"
            else:
                status = "Pending"

            courses.append({
                'id': cid,
                'code': course_code,
                'description': desc,
                'credits': credits,
                'status': status
            })

        # Reset plan POST
        if request.method == "POST" and request.POST.get('reset_plan'):
            request.session['semester_plan'] = []
            return redirect('view_curriculum_plan')

        # Debug logs (optional, remove in production)
        logger.debug(f"Completed courses: {completed_set}")
        logger.debug(f"Planned courses: {planned_set}")

        return render(request, 'accounts/view_curriculum_plan.html', {'courses': courses})

    except Exception as e:
        logger.exception("Error loading curriculum plan")
        return render(request, 'accounts/view_curriculum_plan.html', {'courses': []})


@login_required
def reset_plan(request):
    if request.method == "POST":
        request.user.set_planned_courses([])  # clear all planned courses
        return redirect("view_curriculum_plan")  # redirect back to plan view
    return redirect("view_curriculum_plan")


@login_required
def plan_next_semester(request):
    """ Suggests courses for the next semester. Saves selected courses in session."""
    try:
        # Get user's selected concentration
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        courses = firebase_service.get_curriculum_courses(curriculum_code=curriculum_code)
        completed_ids = getattr(request.user, "completed_courses_json", []) or []

        # Helper: canonicalize ID for consistent comparison
        def canonical_id(cid):
            return str(cid).replace(' ', '').upper()

        # Filter out completed courses
        next_semester_courses = [
            {
                "id": c.get("id", c["code"].replace(" ", "")),
                "course_code": c["code"],
                "course_name": c["description"],
                "credits": c["credits"],
                "component": c["component"]
            }
            for c in courses
            if canonical_id(c.get("id", c["code"].replace(" ", ""))) not in set(canonical_id(x) for x in completed_ids)
        ]

        # Handle saving selected courses
        if request.method == "POST":
            selected = request.POST.getlist("selected_courses")
            canonicalized = [c.replace(' ', '').upper() for c in selected]
            request.user.set_planned_courses(canonicalized)
            return redirect('view_curriculum_plan')

        context = {
            "courses": next_semester_courses,
            "saved_plan": request.user.get_planned_courses(),
            "max_credits": 16,
        }
        return render(request, "accounts/plan_next_semester.html", context)

    except Exception as e:
        logger.exception("Error planning next semester")
        return render(request, "accounts/plan_next_semester.html", {
            "error": "Could not load available courses.",
            "courses": [],
            "saved_plan": [],
            "max_credits": 16,
        })


# --- PolyTech Companion Chatbot (Bubble Assistant) ---
@login_required
@login_required
def ai_chatbot_page(request):
    """
    Renders the PolyTech Companion chatbot page and handles POST requests for chat answers.
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            messages = data.get('messages', [])
            current_url = data.get('current_url', None)
            visible_menu = data.get('visible_menu', None)
            extra_context = data.get('extra_context', None)

            # ===== GET FIREBASE DATA =====
            from firebase_service import firebase_service

            student_info = ""
            if firebase_service.db:
                try:
                    #  Use username for Firebase lookup, not user.id
                    username = request.user.username
                    logger.info(f"Looking up data for logged-in user: {username}")

                    #  Get data from Django model first
                    completed_courses_from_model = getattr(request.user, "completed_courses_json", [])
                    planned_courses_from_model = []
                    if hasattr(request.user, 'get_planned_courses'):
                        planned_courses_from_model = request.user.get_planned_courses()

                    #  Try multiple Firebase lookup methods
                    user_data = None

                    # Method 1: Try document ID as username
                    user_ref = firebase_service.db.collection('users').document(username)
                    user_doc = user_ref.get()

                    if user_doc.exists:
                        user_data = user_doc.to_dict()
                        logger.info(f"Found Firebase data using username as doc ID")
                    else:
                        # Method 2: Try with user.id as string
                        user_id_str = str(request.user.id)
                        user_ref_by_id = firebase_service.db.collection('users').document(user_id_str)
                        user_doc_by_id = user_ref_by_id.get()

                        if user_doc_by_id.exists:
                            user_data = user_doc_by_id.to_dict()
                            logger.info(f"Found Firebase data using user ID")

                    #  Use Firebase data if found, otherwise use Django model data
                    if user_data:
                        completed = user_data.get('completed_courses', completed_courses_from_model)
                        planned = user_data.get('planned_courses', planned_courses_from_model)
                        concentration = user_data.get('selected_concentration',
                                                      getattr(request.user, "selected_concentration",
                                                              'Computer Engineering'))
                        has_setup = user_data.get('has_completed_setup',
                                                  getattr(request.user, "has_completed_setup", False))
                        email = user_data.get('email', request.user.email)
                    else:
                        # No Firebase data, use Django model data
                        logger.info(f"No Firebase data found, using Django model data for {username}")
                        completed = completed_courses_from_model
                        planned = planned_courses_from_model
                        concentration = getattr(request.user, "selected_concentration", 'Computer Engineering')
                        has_setup = getattr(request.user, "has_completed_setup", False)
                        email = request.user.email

                    #  Build student info with clear identification
                    student_info = f"""
STUDENT ACADEMIC INFORMATION:
- Current User: {username} (NOT any other student)
- Name: {request.user.get_full_name() or username}
- Email: {email}
- Concentration: {concentration}
- Completed Courses: {', '.join(completed) if completed else 'None'}
- Number of Completed Courses: {len(completed)}
- Planned Courses: {', '.join(planned) if planned else 'None'}
- Setup Status: {'Complete' if has_setup else 'Incomplete'}

IMPORTANT: This is {username}'s data. Do not confuse with any other student.
The student {username} has completed {len(completed)} courses: {completed}
The student {username} is planning to take: {planned}
"""

                    logger.info(f"AI will receive data for: {username}")
                    logger.info(f"Completed courses count: {len(completed)}")
                    logger.info(f"Courses: {completed}")

                except Exception as e:
                    logger.error(f"Error retrieving user data: {e}", exc_info=True)
                    student_info = f"Unable to access student database: {str(e)}"

            # ===== INJECT STUDENT INFO INTO THE FIRST MESSAGE =====
            if messages and student_info:
                system_prompt = f"""You are PolyTech Companion, an academic advisor chatbot.
You have access to the current logged-in student's academic records.

CRITICAL: You are currently speaking with {request.user.username}. This is NOT Elvis or any other student.

{student_info}

Use this information to provide personalized academic advice for {request.user.username} specifically.
When asked about courses, refer to the specific courses that {request.user.username} has completed or plans to take.
Do NOT use data from any other student."""

                # Insert as system message at the beginning
                messages = [{"role": "system", "content": system_prompt}] + messages

            # Also add to extra_context for redundancy
            if extra_context:
                extra_context = f"{student_info}\n\n{extra_context}"
            else:
                extra_context = student_info

            # Debug logging
            logger.info(f"Sending to AI - First message role: {messages[0].get('role') if messages else 'none'}")
            logger.info(f"Student info included: {'Yes' if student_info else 'No'}")

            # Call the AI with the enriched messages
            reply = ask_openai_chatbot(
                messages,
                current_url=current_url,
                visible_menu=visible_menu,
                extra_context=extra_context
            )

            return JsonResponse({'reply': reply})

        except Exception as e:
            logger.error(f"PolyTech Companion error: {e}", exc_info=True)
            return JsonResponse({'error': 'Network error. Please try again.'}, status=500)

    return render(request, 'accounts/ai_chatbot_page.html')


@login_required
def plan_trimester(request):
    """Display the trimester planning page"""
    try:
        # Get user's selected concentration
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        # Get user's completed courses from the user model
        completed_courses_ids = getattr(request.user, "completed_courses_json", [])

        # Get all courses from user's specific curriculum
        all_courses = firebase_service.get_curriculum_courses(curriculum_code=curriculum_code)

        # Pass completed courses to template
        context = {
            'completed_courses': json.dumps(completed_courses_ids),
            'concentration': getattr(request.user, "selected_concentration", 'Computer Engineering')
        }
        return render(request, 'accounts/plan_trimester.html', context)
    except Exception as e:
        logger.error(f"Error loading plan trimester page: {e}")
        return render(request, 'accounts/plan_trimester.html', {'error': 'Could not load data'})


@login_required
def api_available_courses(request):
    try:
        # Get user's selected concentration
        curriculum_code = getattr(request.user, 'selected_concentration', 'Comp_Eng_2025')

        completed_courses_ids = set(getattr(request.user, "completed_courses_json", []))
        all_courses = firebase_service.get_curriculum_courses(curriculum_code=curriculum_code)
        available_courses = []

        for course in all_courses:
            # Use the course code as ID (already set by firebase_service)
            course_code = course.get('code', '')
            course_id = course.get('id')  # Already set to course code in firebase_service

            if course_id in completed_courses_ids:
                continue

            can_take, missing_prereqs = check_prerequisites(course, completed_courses_ids)

            prereq_str = ', '.join(course.get('prerequisites', [])) if course.get('prerequisites') else ''

            # Get term info for context
            term_info = course.get('term_info', {})
            is_critical = course_code in term_info.get('critical_courses', [])

            course_info = {
                'id': course_id,
                'course_code': course_code,
                'course_name': course.get('title', course.get('description', '')),
                'credits': course.get('credits', 0),
                'prerequisites': prereq_str,
                'corequisites': course.get('corequisites', []),
                'component': course.get('component', ''),
                'term_availability': course.get('term', []),
                'can_take': can_take,
                'missing_prerequisites': missing_prereqs,
                'is_critical': is_critical,
                'term_id': course.get('term_id', ''),
                'recommended_trimester': term_info.get('recommended_trimester', [])
            }
            available_courses.append(course_info)

        return JsonResponse({
            'courses': available_courses,
            'total_available': len([c for c in available_courses if c['can_take']])
        })
    except Exception as e:
        logger.error(f"Error getting available courses: {e}")
        return JsonResponse({'error': str(e)}, status=500)


def check_prerequisites(course, completed_courses):
    """
    Check if prerequisites are met for a course
    Returns: (can_take: bool, missing_prerequisites: list)
    """
    prerequisites = course.get('prerequisites', [])

    # Handle both string and list format for prerequisites
    if isinstance(prerequisites, str):
        if not prerequisites or prerequisites.strip() == '':
            return True, []
        prerequisites = [prerequisites]
    elif not prerequisites or len(prerequisites) == 0:
        return True, []

    missing = []

    for prereq in prerequisites:
        prereq = prereq.strip()

        if not prereq or 'Departmental permit' in prereq or 'Departmental Permit' in prereq:
            continue

        # Prerequisites are already in the same format as course IDs (e.g., "MATH1350")
        # No conversion needed
        if prereq not in completed_courses:
            missing.append(prereq)

    return len(missing) == 0, missing


@login_required
def save_trimester_plan(request):
    """Save a trimester plan to Firebase"""
    if request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            plan_name = data.get('plan_name', 'Untitled Plan')
            selected_courses = data.get('selected_courses', [])
            total_credits = data.get('total_credits', 0)

            # Create plan object
            plan = {
                'plan_id': str(uuid.uuid4()),
                'plan_name': plan_name,
                'selected_courses': selected_courses,
                'total_credits': total_credits,
                'created_at': None  # Firebase will add timestamp
            }

            # Save to Firebase
            user_doc_ref = firebase_service.db.collection('user_curriculum').document(request.user.username)
            user_doc = user_doc_ref.get()

            if user_doc.exists:
                user_data = user_doc.to_dict()
                plans = user_data.get('plans', [])
                plans.append(plan)
                user_doc_ref.update({'plans': plans})
            else:
                # ADD userId when creating new document
                user_doc_ref.set({
                    'userId': str(request.user.id),  # Security: link to Django user
                    'username': request.user.username,  # Keep for reference
                    'plans': [plan]
                })

            return JsonResponse({
                'success': True,
                'message': 'Plan saved successfully',
                'plan_id': plan['plan_id']
            })

        except json.JSONDecodeError:
            logger.error("Invalid JSON in save_trimester_plan")
            return JsonResponse({
                'success': False,
                'errors': ['Invalid JSON data']
            }, status=400)

        except Exception as e:
            logger.error(f"Error saving trimester plan: {e}", exc_info=True)
            return JsonResponse({
                'success': False,
                'errors': ['Failed to save plan. Please try again.']
            }, status=500)

    return JsonResponse({
        'success': False,
        'errors': ['Invalid request']
    }, status=400)


@login_required
def migrate_user_data_add_userid(request):
    """
    ONE-TIME migration to add userId to existing user_curriculum documents
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    try:
        # Get all user_curriculum documents
        user_curriculum_ref = firebase_service.db.collection('user_curriculum')
        docs = user_curriculum_ref.stream()

        migrated_count = 0
        skipped_count = 0
        error_count = 0
        details = []

        for doc in docs:
            username = doc.id
            doc_data = doc.to_dict()

            # Check if userId exists AND is not empty
            has_user_id = 'userId' in doc_data and doc_data.get('userId') is not None and doc_data.get('userId') != ''

            if has_user_id:
                logger.info(f"Skipping {username} - already has userId: {doc_data.get('userId')}")
                skipped_count += 1
                details.append(f"Skipped {username} (already has userId)")
                continue

            # Try to find matching Django user
            try:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                user = User.objects.get(username=username)

                # Get existing data to preserve it
                existing_data = doc_data.copy()

                # Add userId and username fields
                existing_data['userId'] = str(user.id)
                existing_data['username'] = username

                # Update document with all data including new fields
                firebase_service.db.collection('user_curriculum').document(username).set(existing_data)

                migrated_count += 1
                logger.info(f"✅ Migrated user_curriculum for {username} (Django ID: {user.id})")
                details.append(f"✅ Migrated {username} (ID: {user.id})")

            except User.DoesNotExist:
                logger.error(f"❌ No Django user found for username: {username}")
                error_count += 1
                details.append(f"❌ No Django user for {username}")
            except Exception as e:
                logger.error(f"❌ Could not migrate {username}: {e}")
                error_count += 1
                details.append(f"❌ Error for {username}: {str(e)}")

        return JsonResponse({
            'success': True,
            'migrated_count': migrated_count,
            'skipped_count': skipped_count,
            'error_count': error_count,
            'message': f'Migrated {migrated_count} documents, skipped {skipped_count}, {error_count} errors',
            'details': details
        })

    except Exception as e:
        logger.error(f"Migration error: {e}", exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_user_plans(request):
    """Get all saved plans for the current user"""
    try:
        user_doc = firebase_service.db.collection('user_curriculum').document(request.user.username).get()

        if user_doc.exists:
            user_data = user_doc.to_dict()
            plans = user_data.get('plans', [])
            return JsonResponse({'success': True, 'plans': plans})
        else:
            return JsonResponse({'success': True, 'plans': []})
    except Exception as e:
        logger.error(f"Error getting user plans: {e}")
        return JsonResponse({'success': False, 'error': 'Failed to load plans'}, status=500)


@login_required
def edit_plans(request):
    """Display the edit plans page"""
    try:
        context = {
            'concentration': getattr(request.user, "selected_concentration", 'Computer Engineering')
        }
        return render(request, 'accounts/edit_plans.html', context)
    except Exception as e:
        logger.error(f"Error loading edit plans page: {e}")
        return render(request, 'accounts/edit_plans.html', {'error': 'Could not load data'})


@login_required
def delete_plan(request, plan_id):
    """Delete a specific plan"""
    if request.method == 'POST':
        try:
            user_doc_ref = firebase_service.db.collection('user_curriculum').document(request.user.username)
            user_doc = user_doc_ref.get()

            if user_doc.exists:
                user_data = user_doc.to_dict()
                plans = user_data.get('plans', [])

                # Filter out the plan to delete
                updated_plans = [p for p in plans if p.get('plan_id') != plan_id]

                user_doc_ref.update({'plans': updated_plans})

                return JsonResponse({'success': True, 'message': 'Plan deleted successfully'})
            else:
                return JsonResponse({'success': False, 'error': 'No plans found'}, status=404)
        except Exception as e:
            logger.error(f"Error deleting plan: {e}")
            return JsonResponse({'success': False, 'error': 'Failed to delete plan'}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required
def update_plan(request, plan_id):
    """Update a specific plan"""
    if request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            new_plan_name = data.get('plan_name')
            new_courses = data.get('selected_courses')
            new_credits = data.get('total_credits')

            user_doc_ref = firebase_service.db.collection('user_curriculum').document(request.user.username)
            user_doc = user_doc_ref.get()

            if user_doc.exists:
                user_data = user_doc.to_dict()
                plans = user_data.get('plans', [])

                # Find and update the plan
                for plan in plans:
                    if plan.get('plan_id') == plan_id:
                        if new_plan_name:
                            plan['plan_name'] = new_plan_name
                        if new_courses is not None:
                            plan['selected_courses'] = new_courses
                        if new_credits is not None:
                            plan['total_credits'] = new_credits
                        break

                user_doc_ref.update({'plans': plans})

                return JsonResponse({'success': True, 'message': 'Plan updated successfully'})
            else:
                return JsonResponse({'success': False, 'error': 'No plans found'}, status=404)
        except Exception as e:
            logger.error(f"Error updating plan: {e}")
            return JsonResponse({'success': False, 'error': 'Failed to update plan'}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required
def api_completed_courses(request):
    """Return user's completed courses"""
    completed_courses = getattr(request.user, "completed_courses_json", [])
    return JsonResponse({
        'success': True,
        'completed_courses': completed_courses
    })


@login_required
def save_card_style(request):
    """Save user's preferred dashboard card style"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            card_style = data.get('card_style', '')

            # Validate the card style
            valid_styles = ['', 'tilted-style', 'pentagon-style', 'arrow-style', 'diamond-style', 'organic-style',
                            'sharp-style']
            if card_style not in valid_styles:
                return JsonResponse({'success': False, 'error': 'Invalid card style'}, status=400)

            request.user.dashboard_card_style = card_style
            request.user.save()

            return JsonResponse({'success': True, 'message': 'Card style saved successfully'})
        except Exception as e:
            logger.error(f"Error saving card style: {e}")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)


def password_reset_request(request):
    """Handle password reset requests - sends reset email"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email', '').strip()

            if not email:
                return JsonResponse({'success': False, 'error': 'Email is required'}, status=400)

            # Check if user exists with this email
            from django.contrib.auth import get_user_model
            User = get_user_model()

            try:
                user = User.objects.get(email=email)

                # Generate password reset token
                from django.contrib.auth.tokens import default_token_generator
                from django.utils.http import urlsafe_base64_encode
                from django.utils.encoding import force_bytes
                from django.core.mail import send_mail
                from django.conf import settings

                token = default_token_generator.make_token(user)
                uid = urlsafe_base64_encode(force_bytes(user.pk))

                # Create reset link
                reset_link = f"{request.scheme}://{request.get_host()}/accounts/reset-password/{uid}/{token}/"

                # Send email
                subject = 'Password Reset Request - Curriculum Planner'
                message = f"""
Hello {user.username},

You requested a password reset for your Curriculum Planner account.

Click the link below to reset your password:
{reset_link}

This link will expire in 24 hours.

If you didn't request this reset, please ignore this email.

Best regards,
Curriculum Planner Team
"""

                try:
                    send_mail(
                        subject,
                        message,
                        settings.DEFAULT_FROM_EMAIL,
                        [email],
                        fail_silently=False,
                    )
                    logger.info(f"Password reset email sent to {email}")
                    return JsonResponse({
                        'success': True,
                        'message': 'Password reset link has been sent to your email.'
                    })
                except Exception as e:
                    logger.error(f"Error sending email: {e}")
                    # Still return success to avoid revealing if email exists
                    return JsonResponse({
                        'success': True,
                        'message': 'If an account exists with this email, a password reset link has been sent.'
                    })

            except User.DoesNotExist:
                # Don't reveal if user exists for security
                logger.warning(f"Password reset attempted for non-existent email: {email}")
                return JsonResponse({
                    'success': True,
                    'message': 'If an account exists with this email, a password reset link has been sent.'
                })

        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.error(f"Error in password reset: {e}")
            return JsonResponse({'success': False, 'error': 'An error occurred. Please try again.'}, status=500)

    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)


def password_reset_confirm(request, uidb64, token):
    """Handle password reset confirmation page and form submission"""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_decode
    from django.utils.encoding import force_str

    User = get_user_model()

    if request.method == 'GET':
        # Verify token and show reset form
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)

            if default_token_generator.check_token(user, token):
                # Token is valid, render reset form
                return render(request, 'accounts/password_reset_confirm.html', {
                    'uidb64': uidb64,
                    'token': token,
                    'valid_link': True
                })
            else:
                return render(request, 'accounts/password_reset_confirm.html', {
                    'valid_link': False,
                    'error': 'This password reset link is invalid or has expired.'
                })
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return render(request, 'accounts/password_reset_confirm.html', {
                'valid_link': False,
                'error': 'This password reset link is invalid.'
            })

    elif request.method == 'POST':
        # Process password reset
        try:
            data = json.loads(request.body)
            new_password = data.get('password', '').strip()
            confirm_password = data.get('confirm_password', '').strip()

            if not new_password or not confirm_password:
                return JsonResponse({'success': False, 'error': 'Both password fields are required'}, status=400)

            if new_password != confirm_password:
                return JsonResponse({'success': False, 'error': 'Passwords do not match'}, status=400)

            if len(new_password) < 8:
                return JsonResponse({'success': False, 'error': 'Password must be at least 8 characters long'},
                                    status=400)

            # Verify token again
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)

            if default_token_generator.check_token(user, token):
                # Set new password
                user.set_password(new_password)
                user.save()

                logger.info(f"Password reset successful for user: {user.username}")
                return JsonResponse({
                    'success': True,
                    'message': 'Your password has been reset successfully. You can now log in with your new password.'
                })
            else:
                return JsonResponse({'success': False, 'error': 'Invalid or expired reset link'}, status=400)

        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return JsonResponse({'success': False, 'error': 'Invalid reset link'}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.error(f"Error resetting password: {e}")
            return JsonResponse({'success': False, 'error': 'An error occurred. Please try again.'}, status=500)

    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)


# ============================================================
# CURRICULUM MANAGEMENT VIEWS (Staff only)
# ============================================================

@staff_member_required
def create_curriculum(request):
    """
    Create a new curriculum in Firebase.
    Staff-only view that bypasses Django admin ORM issues.
    """
    if request.method == 'POST':
        form = CurriculumCreateForm(request.POST, firebase_service=firebase_service)
        if form.is_valid():
            # Prepare curriculum data for Firebase
            curriculum_data = {
                'concentration': {
                    'title': form.cleaned_data.get('concentration_title', ''),
                    'code': form.cleaned_data.get('concentration_code', ''),
                    'campus': form.cleaned_data.get('concentration_campus', ''),
                    'version': form.cleaned_data.get('concentration_version', ''),
                },
                'metadata': {
                    'academic_years': form.cleaned_data.get('metadata_academic_years'),
                    'total_terms': form.cleaned_data.get('metadata_total_terms'),
                    'summer_terms': form.cleaned_data.get('metadata_summer_terms'),
                    'start_year': form.cleaned_data.get('metadata_start_year'),
                    'trimesters_per_year': form.cleaned_data.get('metadata_trimesters_per_year'),
                },
                'validation_rules': {
                    'prerequisite_enforcement': form.cleaned_data.get('validation_prerequisite_enforcement', False),
                    'corequisite_bundling': form.cleaned_data.get('validation_corequisite_bundling', False),
                    'min_credits_for_full_time': form.cleaned_data.get('validation_min_credits_for_full_time'),
                    'max_credits_per_term': form.cleaned_data.get('validation_max_credits_per_term'),
                    'check_term_availability': form.cleaned_data.get('validation_check_term_availability', False),
                },
                'ai_recommendation_settings': {
                    'prioritize_critical_path': form.cleaned_data.get('ai_prioritize_critical_path', True),
                    'suggest_corequisite_bundles': form.cleaned_data.get('ai_suggest_corequisite_bundles', True),
                    'warn_on_prerequisite_missing': form.cleaned_data.get('ai_warn_on_prerequisite_missing', True),
                    'suggest_summer_courses': form.cleaned_data.get('ai_suggest_summer_courses', False),
                    'allow_overload': form.cleaned_data.get('ai_allow_overload', False),
                    'default_credit_target': form.cleaned_data.get('ai_default_credit_target', 15),
                },
                'graduation_requirements': form.cleaned_data.get('graduation_requirements', []),
            }

            # Create in Firebase
            success = firebase_service.create_curriculum(
                form.cleaned_data['code'],
                curriculum_data
            )

            if success:
                messages.success(request, f"Curriculum '{form.cleaned_data['code']}' created successfully.")
                return redirect('admin:accounts_curriculum_changelist')
            else:
                messages.error(request, "Failed to create curriculum. It may already exist.")
    else:
        form = CurriculumCreateForm(firebase_service=firebase_service)

    context = {
        'form': form,
        'title': 'Add Curriculum',
        'opts': {'app_label': 'accounts', 'model_name': 'curriculum'},
        'has_view_permission': True,
        'site_header': 'Django administration',
        'site_title': 'Django site admin',
        'cancel_url': reverse('admin:accounts_curriculum_changelist'),
    }
    # Try multiple template paths for compatibility
    return render(request, 'admin/accounts/curriculum/curriculum_create.html', context)


@staff_member_required
def create_term(request, curriculum_code):
    """
    Create a new term in an existing curriculum.
    Curriculum code comes from URL - no dropdown confusion.
    """
    # Verify curriculum exists
    if not firebase_service.curriculum_exists(curriculum_code):
        messages.error(request, f"Curriculum '{curriculum_code}' not found.")
        return redirect('admin:accounts_curriculum_changelist')

    # Get curriculum info for display
    curriculum_data = firebase_service.get_curriculum_metadata(curriculum_code)
    curriculum_title = curriculum_data.get('concentration', {}).get('title', curriculum_code)

    if request.method == 'POST':
        form = TermCreateForm(
            request.POST,
            firebase_service=firebase_service,
            curriculum_code=curriculum_code
        )
        if form.is_valid():
            # Prepare term data for Firebase
            term_data = {
                'term_info': {
                    'term_name': form.cleaned_data['term_name'],
                    'description': form.cleaned_data.get('description', ''),
                    'year': form.cleaned_data.get('year'),
                    'trimester': form.cleaned_data.get('trimester', ''),
                    'min_credits': form.cleaned_data.get('min_credits'),
                    'max_credits': form.cleaned_data.get('max_credits'),
                    'total_credits_in_term': form.cleaned_data.get('total_credits_in_term'),
                    'recommended_trimester': form.cleaned_data.get('recommended_trimester', []),
                    'typical_next_terms': form.cleaned_data.get('typical_next_terms', []),
                    'critical_courses': form.cleaned_data.get('critical_courses', []),
                },
                'concentration': {},
            }

            # Create in Firebase
            success = firebase_service.create_term(
                curriculum_code,
                form.cleaned_data['term_id'],
                term_data
            )

            if success:
                messages.success(
                    request,
                    f"Term '{form.cleaned_data['term_id']}' created in curriculum '{curriculum_code}'."
                )
                # Redirect to curriculum detail page
                return redirect('admin:accounts_curriculum_change', object_id=curriculum_code)
            else:
                messages.error(request, "Failed to create term. It may already exist.")
    else:
        form = TermCreateForm(
            firebase_service=firebase_service,
            curriculum_code=curriculum_code
        )

    context = {
        'form': form,
        'title': f'Add Term to {curriculum_title}',
        'curriculum_code': curriculum_code,
        'curriculum_title': curriculum_title,
        'opts': {'app_label': 'accounts', 'model_name': 'term'},
        'has_view_permission': True,
        'site_header': 'Django administration',
        'site_title': 'Django site admin',
        'cancel_url': reverse('admin:accounts_curriculum_change', args=[curriculum_code]),
    }
    return render(request, 'admin/accounts/curriculum/term_create.html', context)


@staff_member_required
def create_course(request, curriculum_code, term_id):
    """
    Create a new course in an existing term.
    Both curriculum_code and term_id come from URL.
    """
    # Verify curriculum and term exist
    if not firebase_service.curriculum_exists(curriculum_code):
        messages.error(request, f"Curriculum '{curriculum_code}' not found.")
        return redirect('admin:accounts_curriculum_changelist')

    if not firebase_service.term_exists(curriculum_code, term_id):
        messages.error(request, f"Term '{term_id}' not found in curriculum '{curriculum_code}'.")
        return redirect('admin:accounts_curriculum_change', object_id=curriculum_code)

    # Get curriculum and term info for display
    curriculum_data = firebase_service.get_curriculum_metadata(curriculum_code)
    curriculum_title = curriculum_data.get('concentration', {}).get('title', curriculum_code)

    # Get term name
    terms_data = firebase_service.get_all_terms(curriculum_code)
    term_name = term_id
    for term in terms_data:
        if term.get('term_id') == term_id:
            term_name = term.get('term_info', {}).get('term_name', term_id)
            break

    if request.method == 'POST':
        form = CourseCreateForm(
            request.POST,
            firebase_service=firebase_service,
            curriculum_code=curriculum_code,
            term_id=term_id
        )
        if form.is_valid():
            # Prepare course data for Firebase
            course_data = {
                'code': form.cleaned_data['code'],
                'title': form.cleaned_data['title'],
                'credits': form.cleaned_data.get('credits', 3),
                'design_credits': form.cleaned_data.get('design_credits', 0),
                'component': form.cleaned_data.get('component', ''),
                'description': form.cleaned_data.get('description', ''),
                'prerequisites': form.cleaned_data.get('prerequisites', ''),
                'corequisites': form.cleaned_data.get('corequisites', ''),
                'term': form.cleaned_data.get('term', ''),
                'OL': form.cleaned_data.get('OL', False),
                'OL_only': form.cleaned_data.get('OL_only', False),
            }

            # Create in Firebase
            success = firebase_service.create_course(
                curriculum_code,
                term_id,
                form.cleaned_data['code'],
                course_data
            )

            if success:
                messages.success(
                    request,
                    f"Course '{form.cleaned_data['code']}' created in term '{term_id}'."
                )
                # Redirect to term detail page
                composite_id = f"{curriculum_code}:{term_id}"
                return redirect('admin:accounts_term_change', object_id=composite_id)
            else:
                messages.error(request, "Failed to create course. It may already exist.")
    else:
        form = CourseCreateForm(
            firebase_service=firebase_service,
            curriculum_code=curriculum_code,
            term_id=term_id
        )

    context = {
        'form': form,
        'title': f'Add Course to {term_name}',
        'curriculum_code': curriculum_code,
        'curriculum_title': curriculum_title,
        'term_id': term_id,
        'term_name': term_name,
        'opts': {'app_label': 'accounts', 'model_name': 'course'},
        'has_view_permission': True,
        'site_header': 'Django administration',
        'site_title': 'Django site admin',
        'cancel_url': reverse('admin:accounts_term_change', args=[f"{curriculum_code}:{term_id}"]),
    }
    return render(request, 'admin/accounts/curriculum/course_create.html', context)