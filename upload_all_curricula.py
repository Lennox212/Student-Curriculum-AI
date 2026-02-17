"""
Script to upload all curriculum folders to Firestore.
Scans for curriculum folders (e.g., Comp_Engi_Terms, Com_Sci_Terms) and uploads them.
"""

import json
import os
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_service import firebase_service

def upload_curriculum_map(db, file_path):
    """Upload the main curriculum map to Firestore."""
    with open(file_path, 'r', encoding='utf-8') as f:
        curriculum_map = json.load(f)
    
    concentration_code = curriculum_map['concentration']['code']
    
    # Create a reference to the curriculum document
    curriculum_ref = db.collection('curricula').document(concentration_code)
    
    # Upload curriculum metadata
    curriculum_ref.set({
        'concentration': curriculum_map['concentration'],
        'metadata': curriculum_map['curriculum_metadata'],
        'graduation_requirements': curriculum_map['graduation_requirements'],
        'validation_rules': curriculum_map['validation_rules'],
        'ai_recommendation_settings': curriculum_map['ai_recommendation_settings'],
        'last_updated': firestore.SERVER_TIMESTAMP
    })
    
    print(f"✅ Uploaded curriculum map: {concentration_code}")
    
    # Upload terms index
    terms_collection = curriculum_ref.collection('terms_index')
    for term_entry in curriculum_map['terms']:
        # Handle both COE and other prefixes
        filename = term_entry['file']
        # Extract term_id by removing the prefix and .json extension
        # e.g., "COE_term_1a.json" -> "term_1a"
        # e.g., "CS_term_1a.json" -> "term_1a"
        term_id = filename.replace('.json', '')
        # Remove common prefixes
        for prefix in ['COE_', 'CECS_', 'CS_', 'COMPSCI_', 'EE_', 'ME_']:
            if term_id.startswith(prefix):
                term_id = term_id.replace(prefix, '', 1)
                break
        
        terms_collection.document(term_id).set(term_entry)
        print(f"   📋 Indexed term: {term_id}")
    
    return concentration_code

def upload_term_file(db, concentration_code, file_path):
    """Upload a single term file to Firestore."""
    with open(file_path, 'r', encoding='utf-8') as f:
        term_data = json.load(f)
    
    # Extract term identifier from filename
    filename = os.path.basename(file_path)
    term_id = filename.replace('.json', '')
    
    # Remove common prefixes to get standardized term_id
    for prefix in ['COE_', 'CECS_', 'CS_', 'COMPSCI_', 'EE_', 'ME_']:
        if term_id.startswith(prefix):
            term_id = term_id.replace(prefix, '', 1)
            break
    
    # Reference to the specific term document
    term_ref = (db.collection('curricula')
                  .document(concentration_code)
                  .collection('terms')
                  .document(term_id))
    
    # Upload term info
    term_ref.set({
        'term_info': term_data['term_info'],
        'concentration': term_data['concentration'],
        'last_updated': firestore.SERVER_TIMESTAMP
    })
    
    # Upload courses as a subcollection
    courses_collection = term_ref.collection('courses')
    for course in term_data['courses']:
        course_code = course['code']
        courses_collection.document(course_code).set(course)
    
    print(f"✅ Uploaded term: {term_id} ({len(term_data['courses'])} courses)")

def upload_curriculum_folder(db, curriculum_folder):
    """Upload all files from a single curriculum folder."""
    folder_name = curriculum_folder.name
    print(f"\n{'='*70}")
    print(f"📁 Processing {folder_name}")
    print(f"{'='*70}")
    
    # Find curriculum map file (ends with _curriculum_map.json)
    curriculum_map_file = None
    for file in curriculum_folder.glob('*_curriculum_map.json'):
        curriculum_map_file = file
        break
    
    if not curriculum_map_file:
        print(f"❌ No curriculum map found in {folder_name}")
        return None
    
    # Upload curriculum map first
    print(f"📤 Uploading curriculum map from {curriculum_map_file.name}...")
    try:
        concentration_code = upload_curriculum_map(db, curriculum_map_file)
        print(f"\n✅ Curriculum map uploaded for: {concentration_code}\n")
    except Exception as e:
        print(f"❌ Error uploading curriculum map: {e}")
        return None
    
    # Find all term files
    term_files = []
    for pattern in ['*_term_*.json', '*term*.json']:
        for file in curriculum_folder.glob(pattern):
            if 'curriculum_map' not in file.name:
                term_files.append(file)
    
    # Sort term files
    term_files.sort()
    
    print(f"📤 Uploading {len(term_files)} term files...")
    successful_uploads = 0
    failed_uploads = []
    
    for term_file in term_files:
        try:
            upload_term_file(db, concentration_code, term_file)
            successful_uploads += 1
        except Exception as e:
            print(f"❌ Error uploading {term_file.name}: {e}")
            failed_uploads.append(term_file.name)
    
    # Summary for this curriculum
    print(f"\n{'─'*70}")
    print(f"📊 {folder_name} SUMMARY")
    print(f"{'─'*70}")
    print(f"✅ Successfully uploaded: {successful_uploads} term files")
    if failed_uploads:
        print(f"❌ Failed uploads: {len(failed_uploads)}")
        for failed in failed_uploads:
            print(f"   - {failed}")
    else:
        print("🎉 All files uploaded successfully!")
    
    return {
        'folder': folder_name,
        'concentration_code': concentration_code,
        'successful': successful_uploads,
        'failed': len(failed_uploads),
        'failed_files': failed_uploads
    }

def main():
    """Main function to upload all curriculum folders to Firestore."""
    print("🚀 Starting Firestore upload for ALL curriculum folders...")
    print("="*70)
    
    # Get database connection
    db = firebase_service.db
    if not db:
        print("❌ Firebase not initialized")
        return
    
    print("✅ Firebase initialized successfully\n")
    
    # Get parent directory (Capstone folder)
    base_dir = Path(__file__).parent.parent
    
    # Find all curriculum folders (folders ending with _Terms or containing term files)
    curriculum_folders = []
    
    # Look for folders with specific patterns
    for folder in base_dir.iterdir():
        if folder.is_dir():
            # Check if folder has curriculum files
            has_curriculum = False
            
            # Check for curriculum map file
            if list(folder.glob('*_curriculum_map.json')):
                has_curriculum = True
            
            # Check for term files
            if list(folder.glob('*_term_*.json')):
                has_curriculum = True
            
            if has_curriculum:
                curriculum_folders.append(folder)
    
    if not curriculum_folders:
        print("❌ No curriculum folders found!")
        print(f"Searched in: {base_dir}")
        return
    
    print(f"Found {len(curriculum_folders)} curriculum folder(s):")
    for folder in curriculum_folders:
        print(f"  📁 {folder.name}")
    print()
    
    # Upload each curriculum folder
    results = []
    for curriculum_folder in curriculum_folders:
        result = upload_curriculum_folder(db, curriculum_folder)
        if result:
            results.append(result)
    
    # Overall summary
    print(f"\n{'='*70}")
    print("📊 OVERALL SUMMARY")
    print(f"{'='*70}")
    
    total_successful = sum(r['successful'] for r in results)
    total_failed = sum(r['failed'] for r in results)
    
    for result in results:
        status = "✅" if result['failed'] == 0 else "⚠️"
        print(f"{status} {result['folder']}: {result['successful']} successful, {result['failed']} failed")
    
    print(f"\n{'─'*70}")
    print(f"Total: {total_successful} successful uploads, {total_failed} failures")
    print(f"{'─'*70}")
    
    print("\n🔥 Firestore structure:")
    print(f"   curricula/")
    for result in results:
        print(f"   ├── {result['concentration_code']}/")
        print(f"   │   ├── (metadata, graduation_requirements, etc.)")
        print(f"   │   ├── terms_index/")
        print(f"   │   └── terms/")
        print(f"   │       └── (term documents with courses)")

if __name__ == '__main__':
    main()
