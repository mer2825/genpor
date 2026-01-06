from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
import os
from django.utils import timezone
from datetime import timedelta
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from asgiref.sync import sync_to_async
import secrets
import string

class Workflow(models.Model):
    name = models.CharField(max_length=100)
    json_file = models.FileField(upload_to='workflows/')
    active_config = models.TextField(blank=True, null=True, help_text="Active JSON configuration for generation. Filled from the configuration panel.")

    def __str__(self):
        return self.name

# --- SIGNAL TO DELETE FILE WHEN WORKFLOW IS DELETED ---
@receiver(post_delete, sender=Workflow)
def delete_workflow_file(sender, instance, **kwargs):
    """Deletes the JSON file from disk when the Workflow is deleted."""
    if instance.json_file:
        if os.path.isfile(instance.json_file.path):
            try:
                os.remove(instance.json_file.path)
            except Exception as e:
                print(f"Error deleting file: {e}")

def character_image_path(instance, filename):
    if instance.user:
        return f'user_images/{instance.user.id}/{instance.character.name}/{filename}'
    return f'character_images/{instance.character.name}/{filename}'

def character_catalog_path(instance, filename):
    return f'character_catalog/{instance.character.name}/{filename}'

def character_workflow_path(instance, filename):
    # Save generated workflows in a specific folder
    if instance.user:
        return f'user_workflows/{instance.user.id}/{instance.character.name}/{filename}'
    return f'character_workflows/{instance.character.name}/{filename}'

class CharacterCategory(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="Category Name")
    class Meta:
        verbose_name = "General Category"
        verbose_name_plural = "General Categories"
    def __str__(self):
        return self.name

class CharacterSubCategory(models.Model):
    name = models.CharField(max_length=50, unique=True, verbose_name="SubCategory Name")
    class Meta:
        verbose_name = "SubCategory"
        verbose_name_plural = "SubCategories"
    def __str__(self):
        return self.name

class Character(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True, help_text="Internal character description, style notes, etc.")
    category = models.ForeignKey(CharacterCategory, on_delete=models.SET_NULL, null=True, blank=False, related_name="characters", help_text="Mandatory: The main classification (e.g., Realistic, Anime).")
    subcategory = models.ForeignKey(CharacterSubCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="characters", help_text="Optional: A specific sub-classification (e.g., Cyberpunk, Fantasy). Only one allowed.")
    base_workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="characters")
    character_config = models.TextField(blank=True, null=True, help_text="Specific JSON configuration for this character.")
    
    # --- NEW FIELD ---
    is_active = models.BooleanField(default=True, verbose_name="Active", help_text="If unchecked, this character will be hidden from the workspace.")

    prompt_prefix = models.TextField(blank=True, null=True, verbose_name="Prompt Prefix (Character)", default="", help_text="PREFIX: Goes BEFORE the user prompt. Use to describe the character (hair, eyes, body).")
    prompt_suffix = models.TextField(blank=True, null=True, verbose_name="Prompt Suffix (Quality)", default="masterpiece, best quality, newest, absurdres, highres, anime coloring,", help_text="SUFFIX: Goes AFTER the user prompt. Use for Quality Tags (score_9...) and style.")
    negative_prompt = models.TextField(blank=True, null=True, verbose_name="Negative Prompt", default="bad anatomy, bad hands, multiple views, abstract, signature, furry, anthro, 2koma, 4koma, comic, (text, watermark), logo, artist signature, patreon logo, patreon username, twitter username, blurred, unfocused, foggy, poorly drawn hands, poorly drawn fingers, bad quality, worst quality, worst detail,", help_text="NEGATIVE: Things you DO NOT want in the image.")
    def __str__(self):
        return self.name

class CharacterCatalogImage(models.Model):
    character = models.ForeignKey(Character, related_name='catalog_images_set', on_delete=models.CASCADE)
    image = models.ImageField(upload_to=character_catalog_path)
    order = models.PositiveIntegerField(default=0, help_text="Order in the carousel")
    class Meta:
        ordering = ['order']
        verbose_name = "Catalog Image"
        verbose_name_plural = "Catalog Images"
    def __str__(self):
        return f"Catalog Image for {self.character.name}"

@receiver(post_delete, sender=CharacterCatalogImage)
def delete_character_catalog_image_file(sender, instance, **kwargs):
    """Deletes image file from disk when a CharacterCatalogImage is deleted."""
    if instance.image:
        if os.path.isfile(instance.image.path):
            try:
                os.remove(instance.image.path)
            except Exception as e:
                print(f"Error deleting catalog image file: {e}")

class CharacterImage(models.Model):
    TYPE_CHOICES = [('Gen_Normal', 'Normal'), ('Gen_UpScaler', 'Upscaler'), ('Gen_FaceDetailer', 'Face Detailer')]
    character = models.ForeignKey(Character, related_name='images', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_images', null=True, blank=True)
    image = models.ImageField(upload_to=character_image_path)
    
    # --- NEW: Field to save the JSON workflow ---
    generation_workflow = models.FileField(upload_to=character_workflow_path, blank=True, null=True, verbose_name="Generation Workflow (JSON)")
    
    description = models.TextField(blank=True)
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    generation_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='Gen_Normal', verbose_name="Generation Type")
    
    def __str__(self):
        if self.user:
            return f"Image by {self.user.username} for {self.character.name}"
        return f"{self.character.name} - {os.path.basename(self.image.name)}"

@receiver(post_delete, sender=CharacterImage)
def delete_character_image_files(sender, instance, **kwargs):
    """Deletes image and workflow files from disk when a CharacterImage is deleted."""
    # Delete the main image file
    if instance.image:
        if os.path.isfile(instance.image.path):
            try:
                os.remove(instance.image.path)
            except Exception as e:
                print(f"Error deleting image file: {e}")
    
    # Delete the associated workflow file
    if instance.generation_workflow:
        if os.path.isfile(instance.generation_workflow.path):
            try:
                os.remove(instance.generation_workflow.path)
            except Exception as e:
                print(f"Error deleting workflow file: {e}")

# --- NEW: GLOBAL TOKEN SETTINGS PANEL ---
class TokenSettings(models.Model):
    INTERVAL_CHOICES = [('DAILY', 'Daily'), ('WEEKLY', 'Weekly'), ('MONTHLY', 'Monthly'), ('NEVER', 'Never')]
    
    default_token_allowance = models.PositiveIntegerField(default=100, help_text="Default tokens assigned to all clients on reset.")
    reset_interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default='MONTHLY', help_text="How often tokens are reset for all clients.")

    def __str__(self):
        return "Global Token Settings"

    def save(self, *args, **kwargs):
        # Ensure there is only one instance of this model
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Prevent deletion
        pass

    @classmethod
    def load(cls):
        # Load the only instance, or create it if it doesn't exist
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='clientprofile')
    tokens_used = models.PositiveIntegerField(default=0)
    bonus_tokens = models.PositiveIntegerField(default=0, help_text="Extra tokens granted via coupons or admin.")
    last_reset_date = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Profile for {self.user.username}"

    @property
    def tokens_remaining(self):
        settings = TokenSettings.load()
        # Formula: (Base + Bonus) - Used
        return (settings.default_token_allowance + self.bonus_tokens) - self.tokens_used

    # --- NEW ASYNC METHOD ---
    @sync_to_async
    def get_tokens_remaining_async(self):
        settings = TokenSettings.load()
        return (settings.default_token_allowance + self.bonus_tokens) - self.tokens_used

    @sync_to_async
    def check_and_reset_tokens(self):
        settings = TokenSettings.load()
        now = timezone.now()
        should_reset = False
        
        if settings.reset_interval == 'NEVER':
            return

        if settings.reset_interval == 'DAILY':
            if (now - self.last_reset_date).days >= 1:
                should_reset = True
        elif settings.reset_interval == 'WEEKLY':
            if (now - self.last_reset_date).days >= 7:
                should_reset = True
        elif settings.reset_interval == 'MONTHLY':
            if (now - self.last_reset_date).days >= 30:
                should_reset = True
        
        if should_reset:
            self.tokens_used = 0
            # Optional: Do bonus tokens expire? Assuming NO for now.
            self.last_reset_date = now
            self.save()

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created and not instance.is_staff:
        ClientProfile.objects.create(user=instance)

class ConnectionConfig(models.Model):
    name = models.CharField(max_length=100, help_text="Ex: Local, Company GPU")
    base_url = models.CharField(max_length=255, help_text="Ex: http://127.0.0.1:8188 or https://your-url.trycloudflare.com")
    is_active = models.BooleanField(default=False, help_text="Check this box to use this connection.")
    class Meta:
        verbose_name = "Connection Configuration"
        verbose_name_plural = "Connection Configurations"
    def save(self, *args, **kwargs):
        if self.is_active:
            ConnectionConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
    def __str__(self):
        status = " (ACTIVE)" if self.is_active else ""
        return f"{self.name} - {self.base_url}{status}"

class CompanySettings(models.Model):
    name = models.CharField(max_length=200, verbose_name="Company Name", default="My Company")
    logo = models.ImageField(upload_to='company_logos/', verbose_name="Logo", blank=True, null=True)
    favicon = models.ImageField(upload_to='company_logos/', verbose_name="Favicon", blank=True, null=True, help_text="Upload a small square image (e.g., 32x32 or 192x192 png/ico) for the browser tab.")
    offer_bar_text = models.CharField(max_length=255, verbose_name="Offer Bar Text", blank=True, null=True, help_text="Text appearing in the top bar (e.g., '🎉 Special Offer!'). Leave empty to hide.")
    app_hero_title = models.CharField(max_length=200, verbose_name="Main Title (Hero)", default="Anime - Realistic Generator", help_text="The large title appearing on the main page.")
    app_hero_description = models.TextField(verbose_name="Main Description (Hero)", blank=True, default="Transform your ideas into art with our powerful AI engine. Create unique characters in seconds.", help_text="The descriptive text below the main title.")
    description = models.TextField(verbose_name="Description (Footer)", blank=True)
    phone = models.CharField(max_length=50, verbose_name="Phone", blank=True)
    email = models.EmailField(verbose_name="Email", blank=True)
    facebook = models.URLField(verbose_name="Facebook", blank=True)
    discord = models.URLField(verbose_name="Discord", blank=True)
    class Meta:
        verbose_name = "Company Settings"
        verbose_name_plural = "Company Settings"
    def save(self, *args, **kwargs):
        # --- OLD FILE CLEANUP LOGIC ---
        try:
            # Get old instance from DB
            old_instance = CompanySettings.objects.get(pk=self.pk)
            
            # Check if logo changed and old file exists
            if old_instance.logo and old_instance.logo != self.logo:
                if os.path.isfile(old_instance.logo.path):
                    os.remove(old_instance.logo.path)
            
            # Check if favicon changed and old file exists
            if old_instance.favicon and old_instance.favicon != self.favicon:
                if os.path.isfile(old_instance.favicon.path):
                    os.remove(old_instance.favicon.path)
        except CompanySettings.DoesNotExist:
            # New instance, nothing to delete
            pass

        # Original logic to ensure only one instance
        if not self.pk and CompanySettings.objects.exists():
            existing = CompanySettings.objects.first()
            self.pk = existing.pk
        
        super().save(*args, **kwargs)
        
    def __str__(self):
        return self.name

class HeroCarouselImage(models.Model):
    company_settings = models.ForeignKey(CompanySettings, related_name='hero_images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='hero_carousel/', verbose_name="Carousel Image")
    caption = models.CharField(max_length=100, blank=True, verbose_name="Caption (Optional)")
    order = models.PositiveIntegerField(default=0, help_text="Order in the carousel")
    class Meta:
        ordering = ['order']
        verbose_name = "Hero Carousel Image"
        verbose_name_plural = "Hero Carousel Images"
    def __str__(self):
        return f"Hero Image {self.order}"
    def delete(self, *args, **kwargs):
        if self.image and os.path.isfile(self.image.path):
            os.remove(self.image.path)
        super().delete(*args, **kwargs)

class ChatMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_history')
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='chat_messages')
    message = models.TextField(blank=True, null=True) # Prompt text or system message
    is_from_user = models.BooleanField(default=True) # True = User, False = AI
    generated_images = models.ManyToManyField(CharacterImage, blank=True, related_name='chat_messages')
    image_count = models.IntegerField(default=0, help_text="Number of images originally generated in this message.")
    timestamp = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['timestamp'] # Chronological order
    def __str__(self):
        sender = self.user.username if self.is_from_user else f"AI ({self.character.name})"
        return f"{sender}: {self.message[:30]}..."

def generate_coupon_code():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

class Coupon(models.Model):
    code = models.CharField(max_length=20, unique=True, default=generate_coupon_code, editable=False)
    tokens = models.PositiveIntegerField(verbose_name="Tokens to Grant")
    is_redeemed = models.BooleanField(default=False)
    redeemed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='redeemed_coupons')
    created_at = models.DateTimeField(auto_now_add=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.code} - {self.tokens} Tokens"
