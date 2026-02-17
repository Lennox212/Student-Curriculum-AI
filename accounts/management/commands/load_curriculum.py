# accounts/management/commands/load_curriculum.py - Complete version with robust cleaning

import json
import os
import re
from django.core.management.base import BaseCommand
from django.conf import settings
import firebase_admin
from firebase_admin import credentials, firestore

class Command(BaseCommand):
    help = 'Load curriculum data from JSON file to Firebase'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='computer_engineering_courses.json',
            help='Path to the JSON file containing curriculum data'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing data first'
        )
        parser.add_argument(
            '--concentration',
            type=str,
            default='computer_engineering',
            help='Concentration name (e.g., computer_engineering, elec_engi_csc_2025)'
        )

    def handle(self, *args, **options):
        file_path = options['file']
        concentration = options['concentration']
        
        self.stdout.write(f"Loading curriculum for: {concentration}")
        
        # Set environment variable for Firebase credentials
        credential_path = os.path.abspath('firebase-credentials.json')
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credential_path
        self.stdout.write(f"Set credentials path to: {credential_path}")
        
        # Initialize Firebase
        if not firebase_admin._apps:
            try:
                firebase_admin.initialize_app()
                self.stdout.write("Firebase initialized successfully")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Firebase initialization failed: {e}"))
                return

        db = firestore.client()
        
        # Clear existing data if requested
        if options['clear']:
            try:
                self.stdout.write("Clearing existing course data...")
                courses_ref = db.collection('courses')
                docs = courses_ref.stream()
                batch = db.batch()
                count = 0
                for doc in docs:
                    batch.delete(doc.reference)
                    count += 1
                    if count % 500 == 0:  # Firestore batch limit
                        batch.commit()
                        batch = db.batch()
                batch.commit()
                self.stdout.write(f"Cleared {count} existing courses")
            except Exception as e:
                self.stdout.write(f"Warning: Could not clear existing data: {e}")

        try:
            # Load your JSON file
            if not os.path.exists(file_path):
                self.stdout.write(self.style.ERROR(f'JSON file not found: {file_path}'))
                return
                
            with open(file_path, 'r') as f:
                courses_data = json.load(f)

            self.stdout.write(f"Loading {len(courses_data)} courses to Firebase...")

            # Process and upload each course
            successful_uploads = 0
            failed_uploads = 0
            
            for i, course in enumerate(courses_data):
                try:
                    # More aggressive document ID cleaning
                    original_code = course['code']
                    doc_id = self.clean_document_id(original_code, i)
                    
                    self.stdout.write(f"DEBUG: '{original_code}' -> '{doc_id}'")

                    # Process prerequisites into an array
                    prerequisites = []
                    if course['prerequisites']:
                        prereq_text = course['prerequisites'].replace(' and ', ', ').replace('&', ', ')
                        prerequisites = [p.strip() for p in prereq_text.split(',')
                                       if p.strip() and 'Departmental' not in p and 'permit' not in p.lower()]

                    # Process corequisites
                    corequisites = []
                    if course['corequisites']:
                        coreq_text = course['corequisites'].replace(' and ', ', ').replace('&', ', ')
                        corequisites = [c.strip() for c in coreq_text.split(',')
                                      if c.strip() and 'Departmental' not in c and 'permit' not in c.lower()]


                    # Create the Firebase document
                    course_doc = {
                        'code': course['code'],
                        'description': course['description'],
                        'credits': float(course['credits']),
                        'design_credits': float(course.get('design_credits', 0)),
                        'prerequisites': prerequisites,
                        'corequisites': corequisites,
                        'component': course['component'],
                        'online_available': course.get('OL', True),
                        'online_only': course.get('OL_only', False),
                        'term_availability': course.get('term', []),
                        'concentration': concentration,
                        'is_active': True,
                        'course_order': i
                    }

                    # Upload to Firebase
                    db.collection('courses').document(doc_id).set(course_doc)
                    self.stdout.write(f"✓ Uploaded: {course['code']}")
                    successful_uploads += 1
                    
                except Exception as e:
                    self.stdout.write(f"❌ Failed to upload {course['code']}: {e}")
                    failed_uploads += 1
                    continue

            # Create curriculum metadata
            try:
                curriculum_doc = {
                    'name': concentration.replace('_', ' ').title(),
                    'concentration_id': concentration,
                    'total_credits': sum(float(course['credits']) for course in courses_data),
                    'total_design_credits': sum(float(course.get('design_credits', 0)) for course in courses_data),
                    'description': f'Bachelor of Science in {concentration.replace("_", " ").title()}',
                    'is_active': True,
                    'course_count': len(courses_data),
                    'successful_uploads': successful_uploads,
                    'failed_uploads': failed_uploads,
                    'created_at': firestore.SERVER_TIMESTAMP
                }

                db.collection('curriculums').document(concentration).set(curriculum_doc)
                self.stdout.write("✓ Created curriculum metadata")
            except Exception as e:
                self.stdout.write(f"Warning: Could not create curriculum metadata: {e}")

            self.stdout.write(
                self.style.SUCCESS(
                    f'Upload complete! Successful: {successful_uploads}, Failed: {failed_uploads}'
                )
            )

        except FileNotFoundError:
            self.stdout.write(
                self.style.ERROR(f'File not found: {file_path}')
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error loading data: {str(e)}')
            )

    def clean_document_id(self, course_code, index):
        """Clean course code to create valid Firestore document ID"""
        doc_id = course_code
        
        # Replace common problematic patterns
        doc_id = doc_id.replace('XXXX', 'ELECTIVE')
        doc_id = doc_id.replace('xxxx', 'elective')
        
        # Replace spaces and special characters with underscores
        doc_id = doc_id.replace(' ', '_')
        doc_id = doc_id.replace('-', '_')
        doc_id = doc_id.replace('/', '_')
        doc_id = doc_id.replace('\\', '_')
        doc_id = doc_id.replace('.', '_')
        doc_id = doc_id.replace('*', 'ALL')
        
        # Remove any remaining special characters except letters, numbers, and underscores
        doc_id = ''.join(c for c in doc_id if c.isalnum() or c == '_')
        
        # Remove consecutive underscores
        while '__' in doc_id:
            doc_id = doc_id.replace('__', '_')
        
        # Remove leading/trailing underscores
        doc_id = doc_id.strip('_')
        
        # Ensure it's not empty and doesn't start with a number
        if not doc_id:
            doc_id = f"COURSE_{index}"
        elif doc_id[0].isdigit():
            doc_id = f"COURSE_{doc_id}"
        
        # Ensure it's not too long (Firestore limit is 1500 bytes, but keep it reasonable)
        if len(doc_id) > 100:
            doc_id = doc_id[:100]
        
        return doc_id

    