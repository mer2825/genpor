from .models import CompanySettings, HeroCarouselImage
import random

def company_data(request):
    """
    Inyecta la configuración de la empresa y una imagen de fondo aleatoria
    en todas las plantillas (incluyendo Login/Signup).
    """
    settings = CompanySettings.objects.first()
    
    # Obtener una imagen aleatoria del carrusel para el fondo
    hero_imgs = list(HeroCarouselImage.objects.all())
    hero_bg = random.choice(hero_imgs) if hero_imgs else None
    
    return {
        'company_settings': settings,
        'hero_bg': hero_bg
    }
