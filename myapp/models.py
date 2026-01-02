from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
import os

class Workflow(models.Model):
    name = models.CharField(max_length=100)
    json_file = models.FileField(upload_to='workflows/')
    active_config = models.TextField(blank=True, null=True, help_text="Active JSON configuration for generation. Filled from the configuration panel.")

    def __str__(self):
        return self.name

def character_image_path(instance, filename):
    # Sube los archivos a MEDIA_ROOT/user_images/<user_id>/<character_name>/<filename>
    if instance.user:
        return f'user_images/{instance.user.id}/{instance.character.name}/{filename}'
    # Fallback para imágenes sin usuario (ej. las del admin o las iniciales)
    return f'character_images/{instance.character.name}/{filename}'

def character_catalog_path(instance, filename):
    return f'character_catalog/{instance.character.name}/{filename}'

class Character(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True, help_text="Internal character description, style notes, etc.")
    base_workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="characters")
    character_config = models.TextField(blank=True, null=True, help_text="Specific JSON configuration for this character.")
    
    # ELIMINADO: catalog_images (Ahora se usa CharacterCatalogImage)
    
    # NUEVO: Prefijo (Calidad y Estilo)
    prompt_prefix = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Prompt Prefix (Quality)",
        default="score_9, score_8_up, score_7_up, score_6_up, source_anime, rating_explicit, (masterpiece, best quality)",
        help_text="PREFIX: Goes BEFORE the user prompt. Use for Quality Tags (score_9...) and style."
    )
    
    # ANTES positive_prompt, AHORA actúa como SUFIJO
    positive_prompt = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Prompt Suffix (Identity)", 
        default="1girl, solo, beautiful woman, detailed skin",
        help_text="SUFFIX: Goes AFTER the user prompt. Use to describe the character (hair, eyes, body)."
    )
    
    negative_prompt = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Negative Prompt",
        default="score_6, score_5, score_4, source_cartoon, 3d, illustration, (worst quality, low quality:1.2), deformed, bad anatomy",
        help_text="NEGATIVE: Things you DO NOT want in the image."
    )

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

    def delete(self, *args, **kwargs):
        if self.image:
            if os.path.isfile(self.image.path):
                os.remove(self.image.path)
        super().delete(*args, **kwargs)

class CharacterImage(models.Model):
    character = models.ForeignKey(Character, related_name='images', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_images', null=True, blank=True)
    image = models.ImageField(upload_to=character_image_path)
    description = models.TextField(blank=True)
    
    # NUEVO: Dimensiones
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)

    def __str__(self):
        if self.user:
            return f"Image by {self.user.username} for {self.character.name}"
        return f"{self.character.name} - {os.path.basename(self.image.name)}"

    def delete(self, *args, **kwargs):
        if self.image:
            if os.path.isfile(self.image.path):
                os.remove(self.image.path)
        super().delete(*args, **kwargs)

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
    
    # BARRA DE OFERTA (NUEVO)
    offer_bar_text = models.CharField(
        max_length=255, 
        verbose_name="Offer Bar Text", 
        blank=True, 
        null=True, 
        help_text="Text appearing in the top bar (e.g., '🎉 Special Offer!'). Leave empty to hide."
    )
    
    # HERO TEXTO
    app_hero_title = models.CharField(max_length=200, verbose_name="Main Title (Hero)", default="Anime - Realistic Generator", help_text="The large title appearing on the main page.")
    app_hero_description = models.TextField(verbose_name="Main Description (Hero)", blank=True, default="Transform your ideas into art with our powerful AI engine. Create unique characters in seconds.", help_text="The descriptive text below the main title.")

    # ELIMINADO: hero_mode, hero_characters (Ahora se usa HeroCarouselImage)

    description = models.TextField(verbose_name="Description (Footer)", blank=True)
    
    phone = models.CharField(max_length=50, verbose_name="Phone", blank=True)
    email = models.EmailField(verbose_name="Email", blank=True)
    
    facebook = models.URLField(verbose_name="Facebook", blank=True)
    discord = models.URLField(verbose_name="Discord", blank=True)
    
    class Meta:
        verbose_name = "Company Settings"
        verbose_name_plural = "Company Settings"

    def save(self, *args, **kwargs):
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
        if self.image:
            if os.path.isfile(self.image.path):
                os.remove(self.image.path)
        super().delete(*args, **kwargs)

# --- NUEVO: MODELO DE HISTORIAL DE CHAT ---
class ChatMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_history')
    character = models.ForeignKey(Character, on_delete=models.CASCADE, related_name='chat_messages')
    message = models.TextField(blank=True, null=True) # El texto del prompt o mensaje del sistema
    is_from_user = models.BooleanField(default=True) # True = Usuario, False = IA
    generated_images = models.ManyToManyField(CharacterImage, blank=True, related_name='chat_messages')
    image_count = models.IntegerField(default=0, help_text="Number of images originally generated in this message.")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp'] # Orden cronológico

    def __str__(self):
        sender = self.user.username if self.is_from_user else f"AI ({self.character.name})"
        return f"{sender}: {self.message[:30]}..."
