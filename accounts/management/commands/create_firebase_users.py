from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from accounts.firebase_auth_service import firebase_auth_service

class Command(BaseCommand):
    help = 'Create Firebase Auth users for all existing Django users'

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.all()
        
        created_count = 0
        error_count = 0
        
        self.stdout.write('Creating Firebase users...\n')
        
        for user in users:
            try:
                firebase_user = firebase_auth_service.create_or_update_firebase_user(user)
                if firebase_user:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'✅ Created Firebase user: {user.username} (ID: {user.id})')
                    )
                else:
                    error_count += 1
                    self.stdout.write(
                        self.style.ERROR(f'❌ Failed for: {user.username}')
                    )
            except Exception as e:
                error_count += 1
                self.stdout.write(
                    self.style.ERROR(f'❌ Error for {user.username}: {str(e)}')
                )
        
        self.stdout.write(
            self.style.SUCCESS(f'\n✅ Created/Updated {created_count} Firebase users')
        )
        if error_count > 0:
            self.stdout.write(
                self.style.WARNING(f'⚠️  {error_count} errors occurred')
            )