from django.db import models
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
    base_workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="characters")
    character_config = models.TextField(blank=True, null=True, help_text="Configuración JSON específica para este personaje.")
    
    positive_prompt = models.TextField(
        blank=True, 
        null=True, 
        default="masterpiece, best quality, ultra-detailed, 8k",
        help_text="Cosas que SIEMPRE quieres en la imagen (ej: obra maestra, mejor calidad)."
    )
    
    negative_prompt = models.TextField(
        blank=True, 
        null=True, 
        default="ugly, deformed, noisy, blurry, low contrast, text, watermark, extra limbs, extra fingers",
        help_text="Cosas que NO quieres en la imagen (ej: deformidades, texto, etc)."
    )

    def __str__(self):
        return self.name

class CharacterImage(models.Model):
    character = models.ForeignKey(Character, related_name='images', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='generated_images', null=True, blank=True)
    image = models.ImageField(upload_to=character_image_path)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        if self.user:
            return f"Imagen de {self.user.username} para {self.character.name}"
        return f"Imagen para {self.character.name} - {os.path.basename(self.image.name)}"

    def delete(self, *args, **kwargs):
        # Borrar el archivo físico antes de borrar el registro
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
    description = models.TextField(verbose_name="Descripción", blank=True)
    
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
