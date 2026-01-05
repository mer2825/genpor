from .models import ClientProfile, CompanySettings

def user_tokens(request):
    if request.user.is_authenticated and not request.user.is_staff:
        try:
            profile = request.user.clientprofile
            # Check reset on every request (or optimize to do it less often)
            profile.check_and_reset_tokens()
            return {'tokens_remaining': profile.tokens_remaining}
        except ClientProfile.DoesNotExist:
            return {'tokens_remaining': 0}
    return {}

def company_data(request):
    # Carga la primera (y única) instancia de CompanySettings
    settings = CompanySettings.objects.first()
    return {'company': settings}
