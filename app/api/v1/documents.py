"""Document endpoints: upload, list, detail, re-run a failed page.

Routes land here in the next step; the router is registered now so the URL
prefix and tags are fixed in one place.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["documents"])
