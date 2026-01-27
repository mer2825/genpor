import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

print("--- DEBUGGING SOCIAL AUTH ---")

# 1. Check Sites
print(f"\nCurrent SITE_ID in settings: {settings.SITE_ID}")
sites = Site.objects.all()
print(f"Sites in DB ({len(sites)}):")
for s in sites:
    print(f" - ID: {s.id}, Domain: {s.domain}, Name: {s.name}")

# 2. Check Social Apps
apps = SocialApp.objects.all()
print(f"\nSocial Apps in DB ({len(apps)}):")
if not apps:
    print("❌ NO SOCIAL APPS FOUND! You need to create one in the Admin.")
else:
    for app in apps:
        print(f" - ID: {app.id}, Provider: {app.provider}, Name: {app.name}")
        print(f"   Client ID: {app.client_id[:10]}...")
        linked_sites = app.sites.all()
        print(f"   Linked Sites ({len(linked_sites)}):")
        for ls in linked_sites:
            print(f"    -> ID: {ls.id}, Domain: {ls.domain}")
        
        if settings.SITE_ID not in [s.id for s in linked_sites]:
            print(f"   ⚠️ WARNING: This app is NOT linked to the current SITE_ID ({settings.SITE_ID})")
        else:
            print("   ✅ Configuration looks correct for this app.")

print("\n-----------------------------")
