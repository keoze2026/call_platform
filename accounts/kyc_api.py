from ninja import Router, Schema, File
from ninja.files import UploadedFile
from django.utils import timezone
from typing import Optional
from accounts.api import JWTAuth

router = Router(tags=["KYC"], auth=JWTAuth())


class KYCIndividualSchema(Schema):
    kyc_type: str = 'individual'
    full_legal_name: str
    date_of_birth: str
    country: str
    address: str
    phone_number: str
    government_id_url: str


class KYCCompanySchema(Schema):
    kyc_type: str = 'company'
    company_legal_name: str
    business_registration_number: str
    tax_id: str
    country: str
    address: str
    phone_number: str
    director_name: str
    business_registration_doc_url: str
    director_id_url: str


class KYCOutSchema(Schema):
    id: str
    kyc_type: str
    status: str
    rejection_reason: str
    full_legal_name: str
    date_of_birth: Optional[str]
    country: str
    address: str
    phone_number: str
    government_id_url: str
    company_legal_name: str
    business_registration_number: str
    tax_id: str
    director_name: str
    business_registration_doc_url: str
    director_id_url: str
    submitted_at: Optional[str]
    created_at: str


def format_kyc(k):
    return {
        'id': str(k.id),
        'kyc_type': k.kyc_type,
        'status': k.status,
        'rejection_reason': k.rejection_reason,
        'full_legal_name': k.full_legal_name,
        'date_of_birth': str(k.date_of_birth) if k.date_of_birth else None,
        'country': k.country,
        'address': k.address,
        'phone_number': k.phone_number,
        'government_id_url': k.government_id_url,
        'company_legal_name': k.company_legal_name,
        'business_registration_number': k.business_registration_number,
        'tax_id': k.tax_id,
        'director_name': k.director_name,
        'business_registration_doc_url': k.business_registration_doc_url,
        'director_id_url': k.director_id_url,
        'submitted_at': str(k.submitted_at) if k.submitted_at else None,
        'created_at': str(k.created_at),
    }


@router.get("/", response={200: KYCOutSchema, 404: dict})
def get_kyc(request):
    from accounts.kyc import KYCVerification
    try:
        kyc = KYCVerification.objects.get(organization=request.auth.organization)
        return 200, format_kyc(kyc)
    except KYCVerification.DoesNotExist:
        return 404, {"detail": "KYC not submitted yet"}


@router.post("/", response={201: KYCOutSchema, 400: dict})
def submit_kyc(request, payload: KYCIndividualSchema):
    from accounts.kyc import KYCVerification
    kyc, created = KYCVerification.objects.get_or_create(
        organization=request.auth.organization,
        defaults={'kyc_type': payload.kyc_type}
    )
    for k, v in payload.dict().items():
        setattr(kyc, k, v)
    kyc.status = KYCVerification.Status.PENDING
    kyc.submitted_at = timezone.now()
    kyc.save()
    return 201, format_kyc(kyc)


@router.post("/company/", response={201: KYCOutSchema, 400: dict})
def submit_company_kyc(request, payload: KYCCompanySchema):
    from accounts.kyc import KYCVerification
    kyc, created = KYCVerification.objects.get_or_create(
        organization=request.auth.organization,
        defaults={'kyc_type': 'company'}
    )
    for k, v in payload.dict().items():
        setattr(kyc, k, v)
    kyc.status = KYCVerification.Status.PENDING
    kyc.submitted_at = timezone.now()
    kyc.save()
    return 201, format_kyc(kyc)


@router.patch("/", response={200: KYCOutSchema, 404: dict})
def update_kyc(request, payload: KYCIndividualSchema):
    from accounts.kyc import KYCVerification
    try:
        kyc = KYCVerification.objects.get(organization=request.auth.organization)
        for k, v in payload.dict(exclude_none=True).items():
            setattr(kyc, k, v)
        kyc.save()
        return 200, format_kyc(kyc)
    except KYCVerification.DoesNotExist:
        return 404, {"detail": "KYC not found"}


@router.post("/documents/upload/", response={200: dict, 400: dict})
def upload_kyc_document(request):
    import uuid, os
    from django.conf import settings
    file = request.FILES.get('file')
    if not file:
        return 400, {"detail": "No file provided"}
    ext = file.name.split('.')[-1].lower()
    if ext not in ['jpg', 'jpeg', 'png', 'pdf']:
        return 400, {"detail": "Only jpg, jpeg, png, pdf allowed"}
    filename = f"kyc/{uuid.uuid4()}.{ext}"
    upload_dir = os.path.join(settings.MEDIA_ROOT, 'kyc')
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(settings.MEDIA_ROOT, filename)
    with open(filepath, 'wb') as f:
        for chunk in file.chunks():
            f.write(chunk)
    base_url = getattr(settings, 'BASE_URL', 'https://avortyx.io')
    return 200, {"url": f"{base_url}/media/{filename}"}
