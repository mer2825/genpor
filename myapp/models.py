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
from django.core.validators import MinValueValidator, MaxValueValidator # IMPORTANTE
import uuid

# --- EXISTING IMAGE MODELS ---

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
    
    # --- CAMBIO DE SEGURIDAD: CASCADE -> PROTECT ---
    base_workflow = models.ForeignKey(Workflow, on_delete=models.PROTECT, related_name="characters")

    character_config = models.TextField(blank=True, null=True, help_text="Specific JSON configuration for this character.")
    
    # --- NEW FIELDS ---
    is_active = models.BooleanField(default=True, verbose_name="Active", help_text="If unchecked, this character will be hidden from the workspace.")
    is_private = models.BooleanField(default=False, verbose_name="Private Character", help_text="If checked, this character will NOT appear in the public list. Users need a code to access it.")

    prompt_prefix = models.TextField(blank=True, null=True, verbose_name="Prompt Prefix (Character)", default="", help_text="PREFIX: Goes BEFORE the user prompt. Use to describe the character (hair, eyes, body).")
    prompt_suffix = models.TextField(blank=True, null=True, verbose_name="Prompt Suffix (Quality)", default="masterpiece, best quality, newest, absurdres, highres, anime coloring,", help_text="SUFFIX: Goes AFTER the user prompt. Use for Quality Tags (score_9...) and style.")
    negative_prompt = models.TextField(blank=True, null=True, verbose_name="Negative Prompt", default="bad anatomy, bad hands, multiple views, abstract, signature, furry, anthro, 2koma, 4koma, comic, (text, watermark), logo, artist signature, patreon logo, patreon username, twitter username, blurred, unfocused, foggy, poorly drawn hands, bad quality, worst quality, worst detail,", help_text="NEGATIVE: Things you DO NOT want in the image.")
    def __str__(self):
        return self.name

# --- PROXY MODEL FOR ADMIN SEPARATION ---
class PrivateCharacter(Character):
    class Meta:
        proxy = True
        verbose_name = "Private Character"
        verbose_name_plural = "Private Characters"

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
    # CAMBIO: Agregado 'Gen_EyeDetailer' a las opciones
    TYPE_CHOICES = [
        ('Gen_Normal', 'Normal'), 
        ('Gen_UpScaler', 'Upscaler'), 
        ('Gen_FaceDetailer', 'Face Detailer'),
        ('Gen_EyeDetailer', 'Eye Detailer') # NUEVO
    ]
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

# --- VIDEO MODELS (NEW) ---

class VideoConnectionConfig(models.Model):
    name = models.CharField(max_length=100, help_text="Ex: Video GPU 1")
    base_url = models.CharField(max_length=255, help_text="Ex: http://127.0.0.1:8188")
    is_active = models.BooleanField(default=False, help_text="Check this box to use this connection for VIDEO generation.")
    
    class Meta:
        verbose_name = "Video Connection Configuration"
        verbose_name_plural = "Video Connection Configurations"
    
    def __str__(self):
        status = " (ACTIVE)" if self.is_active else ""
        return f"{self.name} - {self.base_url}{status}"

class VideoWorkflow(models.Model):
    name = models.CharField(max_length=100)
    json_file = models.FileField(upload_to='video_workflows/')
    active_config = models.TextField(blank=True, null=True, help_text="Active JSON configuration for video generation.")

    def __str__(self):
        return self.name

@receiver(post_delete, sender=VideoWorkflow)
def delete_video_workflow_file(sender, instance, **kwargs):
    if instance.json_file:
        if os.path.isfile(instance.json_file.path):
            try:
                os.remove(instance.json_file.path)
            except Exception as e:
                print(f"Error deleting video workflow file: {e}")

def video_output_path(instance, filename):
    # --- CAMBIO: Incluir nombre del personaje en la ruta ---
    char_name = instance.character.name if instance.character else "Unknown"
    if instance.user:
        return f'user_videos/{instance.user.id}/{char_name}/{filename}'
    return f'generated_videos/{char_name}/{filename}'

class GeneratedVideo(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_videos')
    # --- CAMBIO: Vincular video a un personaje ---
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='videos', null=True, blank=True)
    
    video_file = models.FileField(upload_to=video_output_path)
    thumbnail = models.ImageField(upload_to='video_thumbnails/', blank=True, null=True)
    
    # Metadata
    prompt = models.TextField()
    negative_prompt = models.TextField(blank=True, null=True)
    duration = models.IntegerField(default=3, help_text="Duration in seconds")
    fps = models.IntegerField(default=24)
    width = models.IntegerField(default=1024)
    height = models.IntegerField(default=576)
    seed = models.BigIntegerField(default=0)
    
    # Workflow used
    workflow_used = models.ForeignKey(VideoWorkflow, on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Video by {self.user.username} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"

@receiver(post_delete, sender=GeneratedVideo)
def delete_generated_video_files(sender, instance, **kwargs):
    if instance.video_file:
        if os.path.isfile(instance.video_file.path):
            try:
                os.remove(instance.video_file.path)
            except Exception as e:
                print(f"Error deleting video file: {e}")
    if instance.thumbnail:
        if os.path.isfile(instance.thumbnail.path):
            try:
                os.remove(instance.thumbnail.path)
            except Exception as e:
                print(f"Error deleting thumbnail file: {e}")

# --- NEW: GLOBAL CLIENT SETTINGS (Renamed from TokenSettings) ---
class TokenSettings(models.Model):
    INTERVAL_CHOICES = [('DAILY', 'Daily'), ('WEEKLY', 'Weekly'), ('MONTHLY', 'Monthly'), ('NEVER', 'Never')]
    
    # --- TOKEN CONFIGURATION ---
    default_token_allowance = models.PositiveIntegerField(default=100, help_text="Default tokens assigned to all clients on reset.")
    reset_interval = models.CharField(max_length=10, choices=INTERVAL_CHOICES, default='MONTHLY', help_text="How often tokens are reset for all clients.")

    # --- BASE PLAN PERMISSIONS (FREE USERS) ---
    allow_upscale_free = models.BooleanField(default=False, verbose_name="Allow Upscale (Free Tier)", help_text="If checked, users without a subscription can use Upscale.")
    allow_face_detail_free = models.BooleanField(default=False, verbose_name="Allow Face Detailer (Free Tier)", help_text="If checked, users without a subscription can use Face Detailer.")
    allow_eye_detail_free = models.BooleanField(default=False, verbose_name="Allow Eye Detailer (Free Tier)", help_text="If checked, users without a subscription can use Eye Detailer.")

    def __str__(self):
        return "Global Client Configuration"

    class Meta:
        verbose_name = "Client Configuration"
        verbose_name_plural = "Client Configuration"

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
        # REMOVED: Logic that forced only one active connection
        # if self.is_active:
        #     ConnectionConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)
    def __str__(self):
        status = " (ACTIVE)" if self.is_active else ""
        return f"{self.name} - {self.base_url}{status}"

class CompanySettings(models.Model):
    name = models.CharField(max_length=200, verbose_name="Company Name", default="My Company")
    logo = models.ImageField(upload_to='company_logos/', verbose_name="Logo", blank=True, null=True)
    favicon = models.ImageField(upload_to='company_logos/', verbose_name="Favicon", blank=True, null=True, help_text="Upload a small square image (e.g., 32x32 or 192x192 png/ico) for the browser tab.")
    offer_bar_text = models.CharField(max_length=255, verbose_name="Offer Bar Text", blank=True, null=True, help_text="Text appearing in the top bar (e.g., '🎉 Special Offer!'). Leave empty to hide.")
    
    # --- NEW: OFFER BAR COLOR ---
    offer_bar_color = models.CharField(max_length=7, default="#10b981", verbose_name="Offer Bar Color", help_text="Hex code (e.g., #10b981). Base color for the offer bar.")

    app_hero_title = models.CharField(max_length=200, verbose_name="Main Title (Hero)", default="Anime - Realistic Generator", help_text="The large title appearing on the main page.")
    
    # --- NEW FIELD: SUBTITLE ---
    app_hero_subtitle = models.CharField(max_length=255, verbose_name="Main Subtitle (Hero)", blank=True, null=True, help_text="A smaller title below the main one, but above the description.")
    
    app_hero_description = models.TextField(verbose_name="Main Description (Hero)", blank=True, default="Transform your ideas into art with our powerful AI engine. Create unique characters in seconds.", help_text="The descriptive text below the main title.")
    description = models.TextField(verbose_name="Description (Footer)", blank=True)
    phone = models.CharField(max_length=50, verbose_name="Phone", blank=True)
    email = models.EmailField(verbose_name="Email", blank=True)
    facebook = models.URLField(verbose_name="Facebook", blank=True)
    discord = models.URLField(verbose_name="Discord", blank=True)

    # --- NEW: COLOR CUSTOMIZATION FIELDS ---
    primary_color_start = models.CharField(max_length=7, default="#a855f7", verbose_name="Gradient Start Color", help_text="Hex code (e.g., #a855f7). Starts the gradient (Top-Left).")
    primary_color_mid = models.CharField(max_length=7, default="#ef4444", verbose_name="Gradient Middle Color", help_text="Hex code (e.g., #ef4444). Middle of the gradient.")
    primary_color_end = models.CharField(max_length=7, default="#ff9068", verbose_name="Gradient End Color", help_text="Hex code (e.g., #ff9068). Ends the gradient (Bottom-Right).")
    
    accent_glow_color = models.CharField(max_length=7, default="#ef4444", verbose_name="Accent Glow Color", help_text="Hex code (e.g., #ef4444). Used for shadows and glows.")

    # --- NEW: TOKEN SALE SWITCH ---
    is_token_sale_active = models.BooleanField(default=True, verbose_name="Enable Token Sales", help_text="If unchecked, users cannot buy token packages.")

    # --- NEW: SUBSCRIPTION SWITCH ---
    is_subscription_active = models.BooleanField(default=True, verbose_name="Enable Subscriptions", help_text="If unchecked, users cannot subscribe to plans.")

    # --- NEW: PAYPAL SETTINGS (ADMIN CONFIGURABLE) ---
    paypal_receiver_email = models.EmailField(verbose_name="PayPal Receiver Email", blank=True, null=True, help_text="The email of the PayPal Business account that receives payments.")
    paypal_is_sandbox = models.BooleanField(default=True, verbose_name="PayPal Sandbox Mode", help_text="If checked, payments will be processed in Sandbox (Test) mode. Uncheck for Live (Real Money).")

    # --- NEW: STRIPE SETTINGS (ADMIN CONFIGURABLE) ---
    stripe_publishable_key = models.CharField(max_length=255, verbose_name="Stripe Publishable Key", blank=True, null=True, help_text="Starts with pk_test_ or pk_live_")
    stripe_secret_key = models.CharField(max_length=255, verbose_name="Stripe Secret Key", blank=True, null=True, help_text="Starts with sk_test_ or sk_live_")

    # --- NEW: LEGAL CONTENT ---
    terms_content = models.TextField(verbose_name="Terms & Conditions", blank=True, default="<p>Please add your Terms & Conditions here.</p>", help_text="HTML content for Terms & Conditions modal.")
    privacy_content = models.TextField(verbose_name="Privacy Policy", blank=True, default="<p>Please add your Privacy Policy here.</p>", help_text="HTML content for Privacy Policy modal.")

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

# --- NEW: AUTH PAGE IMAGES ---
class AuthPageImage(models.Model):
    company_settings = models.ForeignKey(CompanySettings, related_name='auth_images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='auth_backgrounds/', verbose_name="Auth Background Image")
    caption = models.CharField(max_length=100, blank=True, verbose_name="Caption (Optional)")
    order = models.PositiveIntegerField(default=0, help_text="Order in the slideshow")
    
    # --- NEW: OPACITY CONTROL ---
    overlay_opacity = models.FloatField(
        default=0.5, 
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        verbose_name="Overlay Opacity",
        help_text="0.0 = Transparent (Image clear), 1.0 = Black (Image hidden). Default is 0.5."
    )

    class Meta:
        ordering = ['order']
        verbose_name = "Auth Page Image"
        verbose_name_plural = "Auth Page Images"
    def __str__(self):
        return f"Auth Image {self.order}"
    def delete(self, *args, **kwargs):
        if self.image and os.path.isfile(self.image.path):
            os.remove(self.image.path)
        super().delete(*args, **kwargs)

class ChatMessage(models.Model):
    # --- CAMBIO: Tipo de chat (Imagen o Video) ---
    CHAT_TYPE_CHOICES = [
        ('IMAGE', 'Image Generation'),
        ('VIDEO', 'Video Generation'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_history')
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='chat_messages')
    message = models.TextField(blank=True, null=True) # Prompt text or system message
    is_from_user = models.BooleanField(default=True) # True = User, False = AI
    
    # Relaciones
    generated_images = models.ManyToManyField(CharacterImage, blank=True, related_name='chat_messages')
    # --- CAMBIO: Relación con videos generados ---
    generated_videos = models.ManyToManyField(GeneratedVideo, blank=True, related_name='chat_messages')
    
    image_count = models.IntegerField(default=0, help_text="Number of images originally generated in this message.")
    
    # --- CAMBIO: Campo para distinguir el tipo de chat ---
    chat_type = models.CharField(max_length=10, choices=CHAT_TYPE_CHOICES, default='IMAGE')
    
    timestamp = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['timestamp'] # Chronological order
    def __str__(self):
        sender = self.user.username if self.is_from_user else f"AI ({self.character.name})"
        return f"{sender} [{self.chat_type}]: {self.message[:30]}..."

def generate_coupon_code():
    return ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))

# --- UPDATED COUPON SYSTEM ---
class Coupon(models.Model):
    code = models.CharField(max_length=20, unique=True, default=generate_coupon_code, editable=True) # Editable to allow custom codes
    tokens = models.PositiveIntegerField(verbose_name="Tokens to Grant", default=0)
    
    # --- NEW: PREMIUM FEATURES GRANT ---
    duration_days = models.PositiveIntegerField(default=0, verbose_name="Duration (Days)", help_text="How many days the premium features last. 0 = No time limit (or just tokens).")
    
    unlock_upscale = models.BooleanField(default=False, verbose_name="Unlock Upscaler", help_text="Does this coupon unlock Upscaler?")
    unlock_face_detail = models.BooleanField(default=False, verbose_name="Unlock Face Detailer", help_text="Does this coupon unlock Face Detailer?")
    unlock_eye_detail = models.BooleanField(default=False, verbose_name="Unlock Eye Detailer", help_text="Does this coupon unlock Eye Detailer?")
    
    # --- NEW FIELDS FOR MULTI-USER ---
    max_redemptions = models.PositiveIntegerField(null=True, blank=True, verbose_name="Max Users", help_text="Maximum number of users who can redeem this coupon. Leave empty for infinite.")
    times_redeemed = models.PositiveIntegerField(default=0, verbose_name="Redeemed Count", editable=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        limit_str = f"{self.times_redeemed}/{self.max_redemptions}" if self.max_redemptions else f"{self.times_redeemed}/∞"
        features = []
        if self.unlock_upscale: features.append("Upscale")
        if self.unlock_face_detail: features.append("Face")
        if self.unlock_eye_detail: features.append("Eye")
        feature_str = f" + {', '.join(features)}" if features else ""
        
        return f"{self.code} - {self.tokens} Tokens{feature_str} ({self.duration_days} Days) (Users: {limit_str})"

class CouponRedemption(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coupon_redemptions')
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='redemptions')
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'coupon') # Prevent double redemption by same user
        verbose_name = "Coupon Redemption"
        verbose_name_plural = "Coupon Redemptions"

    def __str__(self):
        return f"{self.user.username} redeemed {self.coupon.code}"

# --- NEW: USER PREMIUM GRANT (BECAS) ---
class UserPremiumGrant(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='premium_grants')
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, help_text="Source coupon (optional)")
    
    grant_name = models.CharField(max_length=100, default="Premium Access", help_text="Reason for the grant")
    
    expires_at = models.DateTimeField(verbose_name="Expiration Date")
    
    # Snapshot of permissions granted
    grant_upscale = models.BooleanField(default=False)
    grant_face_detail = models.BooleanField(default=False)
    grant_eye_detail = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Premium Grant"
        verbose_name_plural = "User Premium Grants"

    def __str__(self):
        return f"{self.user.username} - {self.grant_name} (Expires: {self.expires_at.strftime('%Y-%m-%d')})"

    @property
    def is_active(self):
        return timezone.now() < self.expires_at

# --- NEW: PRIVATE CHARACTER ACCESS SYSTEM ---

class CharacterAccessCode(models.Model):
    # CHANGE: Removed default=generate_coupon_code to handle it in save() properly
    code = models.CharField(max_length=20, unique=True, blank=True, help_text="Code to unlock the character. Leave empty to auto-generate.")
    
    # CHANGE: ForeignKey -> OneToOneField to enforce 1 key per character
    character = models.OneToOneField(Character, on_delete=models.CASCADE, related_name='access_code', limit_choices_to={'is_private': True})
    
    # --- NEW FIELDS FOR GLOBAL LIMIT ---
    max_redemptions = models.PositiveIntegerField(null=True, blank=True, verbose_name="Max Users", help_text="Maximum number of users who can redeem this code. Leave empty for infinite.")
    times_redeemed = models.PositiveIntegerField(default=0, verbose_name="Redeemed Count", editable=False)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        limit_str = f"{self.times_redeemed}/{self.max_redemptions}" if self.max_redemptions else f"{self.times_redeemed}/∞"
        return f"{self.code} - {self.character.name} (Users: {limit_str})"

    def save(self, *args, **kwargs):
        # AUTO-FILL LOGIC
        if not self.code:
            while True:
                new_code = generate_coupon_code()
                if not CharacterAccessCode.objects.filter(code=new_code).exists():
                    self.code = new_code
                    break
        super().save(*args, **kwargs)

class UserCharacterAccess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='private_characters')
    character = models.ForeignKey(Character, on_delete=models.CASCADE)
    source_code = models.ForeignKey(CharacterAccessCode, on_delete=models.SET_NULL, null=True, blank=True)
    
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'character')
        verbose_name = "User Private Character Access"
        verbose_name_plural = "User Private Character Accesses"

    def __str__(self):
        return f"{self.user.username} -> {self.character.name}"

# --- PAYPAL PAYMENT MODELS ---

class TokenPackage(models.Model):
    name = models.CharField(max_length=100, verbose_name="Package Name", help_text="Ex: Starter Pack")
    tokens = models.PositiveIntegerField(verbose_name="Tokens to Grant", help_text="Amount of tokens in this package")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price (USD)", help_text="Price in USD")
    
    # --- NEW: CUSTOM FEATURES LIST ---
    features_list = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Custom Features List", 
        help_text="Enter features separated by commas (e.g., 'Instant Delivery, Secure Payment'). If empty, defaults will be used."
    )

    is_active = models.BooleanField(default=True, verbose_name="Active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.tokens} Tokens for ${self.price}"

class PaymentTransaction(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    package = models.ForeignKey(TokenPackage, on_delete=models.SET_NULL, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    paypal_transaction_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.amount} - {self.status}"

# --- SUBSCRIPTION MODELS ---

class SubscriptionPlan(models.Model):
    INTERVAL_CHOICES = [
        ('D', 'Days'),
        ('W', 'Weeks'),
        ('M', 'Months'),
        ('Y', 'Years'),
    ]

    name = models.CharField(max_length=100, verbose_name="Plan Name")
    description = models.TextField(blank=True, verbose_name="Description")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Price (USD)")
    
    # PayPal Billing Cycle
    billing_period = models.PositiveIntegerField(default=1, verbose_name="Billing Period (Count)")
    billing_period_unit = models.CharField(max_length=1, choices=INTERVAL_CHOICES, default='M', verbose_name="Billing Period Unit")
    
    tokens_per_period = models.PositiveIntegerField(verbose_name="Tokens per Period", help_text="Tokens granted each renewal")
    
    # --- NEW: PLAN PERMISSIONS ---
    allow_upscale = models.BooleanField(default=False, verbose_name="Allow Upscale", help_text="Does this plan include Upscale?")
    allow_face_detail = models.BooleanField(default=False, verbose_name="Allow Face Detailer", help_text="Does this plan include Face Detailer?")
    allow_eye_detail = models.BooleanField(default=False, verbose_name="Allow Eye Detailer", help_text="Does this plan include Eye Detailer?")

    # --- NEW: CUSTOM FEATURES LIST ---
    features_list = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Custom Features List", 
        help_text="Enter features separated by commas (e.g., 'Priority Support, Cancel Anytime'). If empty, defaults will be used."
    )

    is_active = models.BooleanField(default=True, verbose_name="Active")
    paypal_plan_id = models.CharField(max_length=100, blank=True, null=True, help_text="Optional: ID from PayPal Dashboard if needed")

    def __str__(self):
        return f"{self.name} - ${self.price} / {self.billing_period}{self.billing_period_unit}"

    # --- NEW: HELPER METHOD FOR CAPABILITIES DISPLAY ---
    def get_capabilities_display(self):
        caps = []
        if self.allow_upscale: caps.append("Upscaler")
        if self.allow_face_detail: caps.append("Face Detailer")
        if self.allow_eye_detail: caps.append("Eye Detailer")
        return " + ".join(caps)

class UserSubscription(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('CANCELLED', 'Cancelled'),
        ('EXPIRED', 'Expired'),
        ('PENDING', 'Pending'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    paypal_sub_id = models.CharField(max_length=100, blank=True, null=True, verbose_name="PayPal Subscription ID")
    
    start_date = models.DateTimeField(auto_now_add=True)
    last_payment_date = models.DateTimeField(null=True, blank=True)
    next_payment_date = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan.name if self.plan else 'No Plan'} ({self.status})"

# --- NEW: PAYMENT METHOD CONFIGURATION ---
class PaymentMethod(models.Model):
    name = models.CharField(max_length=50, verbose_name="Method Name", help_text="Ex: Stripe, PayPal")
    config_key = models.CharField(max_length=50, unique=True, verbose_name="Config Key", help_text="Internal key (e.g., 'stripe', 'paypal'). DO NOT CHANGE once set.")
    is_active = models.BooleanField(default=True, verbose_name="Active", help_text="Uncheck to disable this payment method globally.")
    
    class Meta:
        verbose_name = "Payment Method"
        verbose_name_plural = "Payment Methods"

    def __str__(self):
        status = " (ACTIVE)" if self.is_active else " (INACTIVE)"
        return f"{self.name}{status}"

# --- VIDEO CONFIGURATION (GROUPED) ---

class VideoConfiguration(models.Model):
    # Este modelo actúa como contenedor
    def __str__(self):
        return "Video General Settings"

    class Meta:
        verbose_name = "Video Configuration"
        verbose_name_plural = "Video Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(pk=1)
        return obj

class VideoDurationOption(models.Model):
    # Vinculamos al padre
    config = models.ForeignKey(VideoConfiguration, on_delete=models.CASCADE, related_name='durations', default=1)
    duration = models.PositiveIntegerField(verbose_name="Duration (seconds)", help_text="Ex: 3, 5, 9")
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['duration']
        verbose_name = "Duration Option"
        verbose_name_plural = "Duration Options"

    def __str__(self):
        return f"{self.duration}s"

class VideoQualityOption(models.Model):
    # Vinculamos al padre
    config = models.ForeignKey(VideoConfiguration, on_delete=models.CASCADE, related_name='qualities', default=1)
    name = models.CharField(max_length=50, verbose_name="Label", help_text="Ex: High, Medium, Low")
    value = models.PositiveIntegerField(default=25, verbose_name="Quality Value", help_text="Value to send to the workflow (e.g. steps)")
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = "Quality Option"
        verbose_name_plural = "Quality Options"

    def __str__(self):
        return f"{self.name} ({self.value})"