import firebase_admin
from firebase_admin import credentials, firestore
import os
import logging
import json
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

logger = logging.getLogger(__name__)


class FirebaseService:
    _instance = None
    _db = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _initialize_firebase(self):
        """Initialize Firebase connection lazily"""
        if self._initialized:
            return

        try:
            if not firebase_admin._apps:
                # Set environment variable if not already set
                if 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ:
                    credential_path = os.path.abspath('firebase-credentials.json')
                    if os.path.exists(credential_path):
                        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credential_path

                firebase_admin.initialize_app()

            self._db = firestore.client()
            self._initialized = True
            logger.info("Firebase initialized successfully")
        except Exception as e:
            logger.error(f"Firebase initialization failed: {e}")
            self._db = None

    @property
    def db(self):
        """Get Firestore database instance"""
        if not self._initialized:
            self._initialize_firebase()
        return self._db

    def get_curriculum_courses(self, curriculum_code='Comp_Eng_2025'):
        """
        Get all courses from the new term-based curriculum structure.
        Returns a flat list of all courses across all terms, with preparatory courses first.
        Deduplicates courses by course code to prevent showing the same course multiple times.
        """
        try:
            if not self.db:
                return []

            # Get all term documents from the new structure
            terms_ref = self.db.collection('curricula').document(curriculum_code).collection('terms')
            term_docs = terms_ref.stream()

            preparatory_courses = []
            regular_courses = []
            seen_course_codes = set()  # Track seen course codes to avoid duplicates

            for term_doc in term_docs:
                term_data = term_doc.to_dict()
                term_id = term_doc.id
                term_info = term_data.get('term_info', {})
                is_preparatory = term_info.get('is_preparatory', False)

                # Get courses subcollection for this term
                courses_ref = term_doc.reference.collection('courses')
                course_docs = courses_ref.stream()

                for course_doc in course_docs:
                    course_data = course_doc.to_dict()
                    course_code = course_data.get('code', course_doc.id)

                    # Skip if we've already added this course
                    if course_code in seen_course_codes:
                        continue

                    seen_course_codes.add(course_code)

                    # Use course code as ID (e.g., "MATH1350") instead of Firestore doc ID
                    # This ensures compatibility with completed courses stored by code
                    course_data['id'] = course_code
                    course_data['term_id'] = term_id
                    course_data['term_info'] = term_info
                    course_data['is_preparatory'] = is_preparatory

                    # Separate preparatory from regular courses
                    if is_preparatory:
                        preparatory_courses.append(course_data)
                    else:
                        regular_courses.append(course_data)

            # Return preparatory courses first, then regular courses
            all_courses = preparatory_courses + regular_courses
            total_credits = sum(c.get('credits', 0) for c in all_courses)
            logger.info(
                f"firebase_service.get_curriculum_courses: Returning {len(all_courses)} unique courses, {total_credits} total credits")
            return all_courses
        except Exception as e:
            logger.error(f"Error fetching courses: {e}")
            return []

    def get_curriculum_metadata(self, curriculum_code='Comp_Eng_2025'):
        """Get curriculum map metadata"""
        try:
            if not self.db:
                return {}

            doc = self.db.collection('curricula').document(curriculum_code).get()
            if doc.exists:
                return doc.to_dict()
            return {}
        except Exception as e:
            logger.error(f"Error fetching curriculum metadata: {e}")
            return {}

    def get_term_courses(self, curriculum_code='Comp_Eng_2025', term_id=None):
        """Get courses for a specific term"""
        try:
            if not self.db:
                return []

            term_ref = self.db.collection('curricula').document(curriculum_code).collection('terms').document(term_id)
            term_doc = term_ref.get()

            if not term_doc.exists:
                return []

            term_data = term_doc.to_dict()
            courses_ref = term_ref.collection('courses')
            course_docs = courses_ref.stream()

            courses = []
            for course_doc in course_docs:
                course_data = course_doc.to_dict()
                # Use course code as ID for consistency with completed courses
                course_data['id'] = course_data.get('code', course_doc.id)
                course_data['term_id'] = term_id
                course_data['term_info'] = term_data.get('term_info', {})
                courses.append(course_data)

            return courses
        except Exception as e:
            logger.error(f"Error fetching term courses: {e}")
            return []

    def get_all_terms(self, curriculum_code='Comp_Eng_2025'):
        """Get all terms with their metadata"""
        try:
            if not self.db:
                return []

            terms_ref = self.db.collection('curricula').document(curriculum_code).collection('terms')
            term_docs = terms_ref.stream()

            terms = []
            for term_doc in term_docs:
                term_data = term_doc.to_dict()
                term_data['term_id'] = term_doc.id
                terms.append(term_data)

            return terms
        except Exception as e:
            logger.error(f"Error fetching terms: {e}")
            return []

    def get_all_curricula(self):
        """
        Get all curricula with their metadata.
        Returns a list of all curriculum documents from Firestore.
        """
        try:
            if not self.db:
                logger.error("Firestore database not initialized")
                return []

            curricula_ref = self.db.collection('curricula')
            curricula_docs = curricula_ref.stream()

            curricula = []
            for doc in curricula_docs:
                curriculum_data = doc.to_dict()
                # Add document ID as the code field
                curriculum_data['code'] = doc.id
                curricula.append(curriculum_data)

            logger.info(f"Retrieved {len(curricula)} curricula from Firestore")
            return curricula

        except Exception as e:
            logger.error(f"Error fetching curricula: {e}")
            return []

    # ===== CREATION METHODS =====

    def curriculum_exists(self, curriculum_code):
        """Check if a curriculum with the given code already exists."""
        try:
            if not self.db:
                return False
            doc = self.db.collection('curricula').document(curriculum_code).get()
            return doc.exists
        except Exception as e:
            logger.error(f"Error checking curriculum existence: {e}")
            return False

    def course_exists_in_curriculum(self, curriculum_code, course_code):
        """
        Check if a course code exists anywhere in the curriculum.
        Used for prerequisite validation.
        """
        try:
            if not self.db:
                return False

            # Get all terms in the curriculum
            terms_ref = self.db.collection('curricula').document(curriculum_code).collection('terms')
            term_docs = terms_ref.stream()

            for term_doc in term_docs:
                # Check courses subcollection in each term
                courses_ref = term_doc.reference.collection('courses')
                course_doc = courses_ref.document(course_code).get()
                if course_doc.exists:
                    return True

            return False
        except Exception as e:
            logger.error(f"Error checking course existence: {e}")
            return False

    def get_all_course_codes_in_curriculum(self, curriculum_code):
        """
        Get all course codes in a curriculum.
        Used for prerequisite validation dropdown/autocomplete.
        """
        try:
            if not self.db:
                return []

            course_codes = set()
            terms_ref = self.db.collection('curricula').document(curriculum_code).collection('terms')
            term_docs = terms_ref.stream()

            for term_doc in term_docs:
                courses_ref = term_doc.reference.collection('courses')
                course_docs = courses_ref.stream()
                for course_doc in course_docs:
                    course_data = course_doc.to_dict()
                    course_codes.add(course_data.get('code', course_doc.id))

            return sorted(list(course_codes))
        except Exception as e:
            logger.error(f"Error getting course codes: {e}")
            return []

    def term_exists(self, curriculum_code, term_id):
        """Check if a term exists in a curriculum."""
        try:
            if not self.db:
                return False
            term_ref = (self.db.collection('curricula')
                        .document(curriculum_code)
                        .collection('terms')
                        .document(term_id))
            return term_ref.get().exists
        except Exception as e:
            logger.error(f"Error checking term existence: {e}")
            return False

    def course_exists_in_term(self, curriculum_code, term_id, course_code):
        """Check if a course exists in a specific term."""
        try:
            if not self.db:
                return False
            course_ref = (self.db.collection('curricula')
                          .document(curriculum_code)
                          .collection('terms')
                          .document(term_id)
                          .collection('courses')
                          .document(course_code))
            return course_ref.get().exists
        except Exception as e:
            logger.error(f"Error checking course existence in term: {e}")
            return False

    def create_curriculum(self, curriculum_code, data):
        """
        Create a new curriculum document in Firebase.

        Args:
            curriculum_code: Unique identifier for the curriculum (e.g., 'Comp_Eng_2025')
            data: Dictionary containing curriculum data (concentration, metadata, etc.)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.db:
                logger.error("Firebase not initialized")
                return False

            # Check if curriculum already exists
            if self.curriculum_exists(curriculum_code):
                logger.error(f"Curriculum '{curriculum_code}' already exists")
                return False

            # Set the document with the provided data
            curriculum_ref = self.db.collection('curricula').document(curriculum_code)

            # Ensure code is in the data
            data['code'] = curriculum_code

            curriculum_ref.set(data)
            logger.info(f"Created curriculum: {curriculum_code}")
            return True

        except Exception as e:
            logger.error(f"Error creating curriculum: {e}")
            return False

    def create_term(self, curriculum_code, term_id, data):
        """
        Create a new term in a curriculum.

        Args:
            curriculum_code: The curriculum to add the term to
            term_id: Unique identifier for the term (e.g., 'term_1')
            data: Dictionary containing term data (term_info, etc.)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.db:
                logger.error("Firebase not initialized")
                return False

            # Check if curriculum exists
            if not self.curriculum_exists(curriculum_code):
                logger.error(f"Curriculum '{curriculum_code}' does not exist")
                return False

            # Check if term already exists
            if self.term_exists(curriculum_code, term_id):
                logger.error(f"Term '{term_id}' already exists in curriculum '{curriculum_code}'")
                return False

            term_ref = (self.db.collection('curricula')
                        .document(curriculum_code)
                        .collection('terms')
                        .document(term_id))

            term_ref.set(data)
            logger.info(f"Created term: {term_id} in curriculum: {curriculum_code}")
            return True

        except Exception as e:
            logger.error(f"Error creating term: {e}")
            return False

    def create_course(self, curriculum_code, term_id, course_code, data):
        """
        Create a new course in a term.

        Args:
            curriculum_code: The curriculum containing the term
            term_id: The term to add the course to
            course_code: Unique identifier for the course (e.g., 'MATH1350')
            data: Dictionary containing course data

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            if not self.db:
                logger.error("Firebase not initialized")
                return False

            # Check if term exists
            if not self.term_exists(curriculum_code, term_id):
                logger.error(f"Term '{term_id}' does not exist in curriculum '{curriculum_code}'")
                return False

            # Check if course already exists in this term
            if self.course_exists_in_term(curriculum_code, term_id, course_code):
                logger.error(f"Course '{course_code}' already exists in term '{term_id}'")
                return False

            course_ref = (self.db.collection('curricula')
                          .document(curriculum_code)
                          .collection('terms')
                          .document(term_id)
                          .collection('courses')
                          .document(course_code))

            # Ensure code is in the data
            data['code'] = course_code

            course_ref.set(data)
            logger.info(f"Created course: {course_code} in term: {term_id}")
            return True

        except Exception as e:
            logger.error(f"Error creating course: {e}")
            return False

    def validate_prerequisites(self, curriculum_code, prerequisites_str):
        """
        Validate that all prerequisites exist in the curriculum.

        Args:
            curriculum_code: The curriculum to check against
            prerequisites_str: Comma-separated string of prerequisite course codes

        Returns:
            tuple: (is_valid, list_of_missing_courses)
        """
        if not prerequisites_str or not prerequisites_str.strip():
            return True, []

        # Parse prerequisites - handle various formats
        # Could be: "MATH1350" or "MATH1350, MATH1351" or "MATH1350 and MATH1351"
        prereq_str = prerequisites_str.replace(' and ', ', ').replace(' or ', ', ')
        prereq_codes = [code.strip() for code in prereq_str.split(',') if code.strip()]

        # Filter out non-course patterns (like "None" or descriptive text)
        prereq_codes = [code for code in prereq_codes if code.upper()[:4].isalpha() and len(code) >= 6]

        missing = []
        for code in prereq_codes:
            if not self.course_exists_in_curriculum(curriculum_code, code):
                missing.append(code)

        return len(missing) == 0, missing

    def get_user_data_for_ai(self, user_id):
        """
        Get all user data from Firebase for AI context.
        This method fetches existing data without modifying any database structure.
        """
        try:
            if not self.db:
                return {}

            context_data = {}

            # Try to get user document if it exists
            try:
                user_ref = self.db.collection('users').document(str(user_id))
                user_doc = user_ref.get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    # Verify userId matches (security check)
                    if user_data.get('userId') == str(user_id):
                        context_data['user_profile'] = user_data
            except:
                pass

            # Get courses for the user's concentration
            concentration = context_data.get('user_profile', {}).get('selected_concentration', 'computer_engineering')
            context_data['available_courses'] = self.get_curriculum_courses(concentration)

            # Add completed and planned courses if they exist
            if 'user_profile' in context_data:
                context_data['completed_courses'] = context_data['user_profile'].get('completed_courses', [])
                context_data['planned_courses'] = context_data['user_profile'].get('planned_courses', [])

            return context_data

        except Exception as e:
            logger.error(f"Error getting user data for AI: {e}")
            return {}


# Singleton instance
firebase_service = FirebaseService()


# ===== VIEW FUNCTION  =====
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
            student_info = ""
            if firebase_service.db:
                try:
                    # Get current logged-in user's username
                    username = request.user.username
                    logger.info(f"Getting data for logged-in user: {username}")

                    # Use the existing get_user_data_for_ai method
                    user_data = firebase_service.get_user_data_for_ai(username)

                    # Log what we got
                    logger.info(f"Firebase data source: {user_data.get('source', 'unknown')}")

                    # Extract data from the response
                    if 'error' not in user_data or user_data.get('source') != 'not_found':
                        # We have valid data (either from Firebase or with defaults)
                        completed = user_data.get('completed_courses', [])
                        planned = user_data.get('planned_courses', [])
                        concentration = user_data.get('selected_concentration', 'Computer Engineering')
                        has_setup = user_data.get('has_completed_setup', False)
                        email = user_data.get('email', request.user.email)
                    else:
                        # Firebase data not found, fall back to Django model data
                        logger.info(f"Firebase data not found for {username}, using Django model data")
                        completed = getattr(request.user, "completed_courses_json", [])
                        planned = request.user.get_planned_courses() if hasattr(request.user,
                                                                                'get_planned_courses') else []
                        concentration = getattr(request.user, "selected_concentration", 'Computer Engineering')
                        has_setup = getattr(request.user, "has_completed_setup", False)
                        email = request.user.email

                    # Build detailed student information
                    student_info = f"""
STUDENT ACADEMIC INFORMATION:
- Current Logged-In User: {username}
- Name: {request.user.get_full_name() or username}
- Email: {email}
- Concentration: {concentration}
- Completed Courses: {', '.join(completed) if completed else 'None'}
- Number of Completed Courses: {len(completed)}
- Planned Courses: {', '.join(planned) if planned else 'None'}
- Setup Status: {'Complete' if has_setup else 'Incomplete'}

IMPORTANT: You are currently helping {username}. This data belongs to {username}, not any other student.
The student {username} has completed {len(completed)} courses: {completed}
The student {username} is planning to take: {planned}
"""

                    # Log for debugging
                    logger.info(f"Sending to AI - Student: {username}")
                    logger.info(f"Completed courses: {completed}")
                    logger.info(f"Number of completed courses: {len(completed)}")

                except Exception as e:
                    logger.error(f"Error retrieving user data: {e}", exc_info=True)
                    # Fallback to basic info
                    username = request.user.username
                    student_info = f"""
STUDENT ACADEMIC INFORMATION:
- Current User: {username}
- Email: {request.user.email}
- Unable to retrieve complete academic data due to error: {str(e)}
"""
            else:
                # Firebase not initialized
                username = request.user.username
                student_info = f"""
STUDENT ACADEMIC INFORMATION:
- Current User: {username}
- Email: {request.user.email}
- Database connection not available
"""

            # ===== INJECT STUDENT INFO INTO THE FIRST MESSAGE =====
            #  ensures the AI ALWAYS sees the student data
            if messages and student_info:
                # Add student info to the context of the conversation
                system_prompt = f"""You are PolyTech Companion, an academic advisor chatbot for Polytechnic University of Puerto Rico.

CRITICAL: You are currently speaking with {request.user.username}. Use only this student's data for your responses.

{student_info}

Use this information to provide personalized academic advice specifically for {request.user.username}.
When asked about courses, refer to the specific courses that {request.user.username} has completed or plans to take.
Never confuse this student with any other student in the system."""

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
            logger.info(f"Current user in session: {request.user.username}")

            # Call the AI with the enriched messages
            # NOTE: You'll need to import ask_openai_chatbot from wherever it's defined
            # from .ai_utils import ask_openai_chatbot  # Example import
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