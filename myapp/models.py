from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
import os

class Workflow(models.Model):
    name = models.CharField(max_length=100)
    json_file = models.FileField(upload_to='workflows/')
    active_config = models.TextField(blank=True, null=True, help_text="Configuración JSON activa para la generación. Se rellena desde el panel de configuración.")

    def __str__(self):
        return self.name

def character_image_path(instance, filename):
    # Sube los archivos a MEDIA_ROOT/user_images/<user_id>/<character_name>/<filename>
    if instance.user:
        return f'user_images/{instance.user.id}/{instance.character.name}/{filename}'
    # Fallback para imágenes sin usuario (ej. las del admin o las iniciales)
    return f'character_images/{instance.character.name}/{filename}'

class Character(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True, help_text="Descripción interna del personaje, notas sobre su estilo, etc.")
    base_workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="characters")
    character_config = models.TextField(blank=True, null=True, help_text="Configuración JSON específica para este personaje.")
    
    # NUEVO: Prefijo (Calidad y Estilo)
    prompt_prefix = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Prompt Prefijo (Calidad)",
        default="score_9, score_8_up, score_7_up, score_6_up, source_anime, rating_explicit, (masterpiece, best quality)",
        help_text="PREFIJO: Va ANTES del prompt del usuario. Úsalo para Quality Tags (score_9...) y estilo."
    )
    
    # ANTES positive_prompt, AHORA actúa como SUFIJO
    positive_prompt = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Prompt Sufijo (Identidad)", # CAMBIO VISUAL AQUÍ
        default="1girl, solo, beautiful woman, detailed skin",
        help_text="SUFIJO: Va DESPUÉS del prompt del usuario. Úsalo para describir al personaje (pelo, ojos, cuerpo)."
    )
    
    negative_prompt = models.TextField(
        blank=True, 
        null=True, 
        verbose_name="Prompt Negativo",
        default="score_6, score_5, score_4, source_cartoon, 3d, illustration, (worst quality, low quality:1.2), deformed, bad anatomy",
        help_text="NEGATIVO: Cosas que NO quieres en la imagen."
    )

    def __str__(self):
        return self.name

class CharacterImage(models.Model):
    character = models.ForeignKey(Character, related_name='images', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_images', null=True, blank=True)
    image = models.ImageField(upload_to=character_image_path)
    description = models.TextField(blank=True)

    def __str__(self):
        if self.user:
            return f"Imagen de {self.user.username} para {self.character.name}"
        return f"{self.character.name} - {os.path.basename(self.image.name)}"

    def delete(self, *args, **kwargs):
        if self.image:
            if os.path.isfile(self.image.path):
                os.remove(self.image.path)
        super().delete(*args, **kwargs)

class ConnectionConfig(models.Model):
    name = models.CharField(max_length=100, help_text="Ej: Local, GPU Empresa")
    base_url = models.CharField(max_length=255, help_text="Ej: http://127.0.0.1:8188 o https://tu-url.trycloudflare.com")
    is_active = models.BooleanField(default=False, help_text="Marca esta casilla para usar esta conexión.")

    class Meta:
        verbose_name = "Configuración de Conexión"
        verbose_name_plural = "Configuraciones de Conexión"

    def save(self, *args, **kwargs):
        if self.is_active:
            ConnectionConfig.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        status = " (ACTIVA)" if self.is_active else ""
        return f"{self.name} - {self.base_url}{status}"

class CompanySettings(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nombre de la Empresa", default="Mi Empresa")
    logo = models.ImageField(upload_to='company_logos/', verbose_name="Logo", blank=True, null=True)
    
    # HERO TEXTO
    app_hero_title = models.CharField(max_length=200, verbose_name="Título Principal (Hero)", default="Generador Anime - Realista", help_text="El título grande que aparece en la página principal.")
    app_hero_description = models.TextField(verbose_name="Descripción Principal (Hero)", blank=True, default="Transforma tus ideas en arte con nuestro potente motor de IA. Crea personajes únicos en segundos.", help_text="El texto descriptivo debajo del título principal.")

    # HERO CARRUSEL (MODIFICADO: PERSONAJES)
    HERO_MODES = [
        ('random', 'Aleatorio (Automático)'),
        ('manual', 'Manual (Seleccionado)')
    ]
    hero_mode = models.CharField(max_length=10, choices=HERO_MODES, default='random', verbose_name="Modo del Carrusel")
    
    # CAMBIO: Ahora seleccionamos Personajes, no imágenes
    hero_characters = models.ManyToManyField(
        Character, 
        blank=True, 
        verbose_name="Personajes del Carrusel",
        help_text="Selecciona hasta 6 personajes. Se mostrará la primera imagen disponible de cada uno."
    )

    description = models.TextField(verbose_name="Descripción (Footer)", blank=True)
    
    phone = models.CharField(max_length=50, verbose_name="Teléfono", blank=True)
    email = models.EmailField(verbose_name="Correo Electrónico", blank=True)
    
    facebook = models.URLField(verbose_name="Facebook", blank=True)
    discord = models.URLField(verbose_name="Discord", blank=True)
    
    class Meta:
        verbose_name = "Configuración de Empresa"
        verbose_name_plural = "Configuración de Empresa"

    def save(self, *args, **kwargs):
        if not self.pk and CompanySettings.objects.exists():
            existing = CompanySettings.objects.first()
            self.pk = existing.pk
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
