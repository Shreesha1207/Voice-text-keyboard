from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid

from database import get_db
from models import User
from security import SECRET_KEY, ALGORITHM
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Maps deprecated/legacy IANA timezone names to their current canonical equivalents.
# The 'Asia/Calcutta' alias is the most common: browsers on older Android/iOS devices
# and some OS versions still report it instead of 'Asia/Kolkata'. Without this the
# ZoneInfo() call raises ZoneInfoNotFoundError on Linux servers that don't ship the
# backward-compatibility tzdata aliases.
_TZ_ALIASES: dict[str, str] = {
    "Asia/Calcutta":   "Asia/Kolkata",
    "Asia/Katmandu":   "Asia/Kathmandu",
    "Asia/Dacca":      "Asia/Dhaka",
    "Asia/Saigon":     "Asia/Ho_Chi_Minh",
    "Asia/Rangoon":    "Asia/Yangon",
    "Asia/Chongqing":  "Asia/Shanghai",
    "Asia/Harbin":     "Asia/Shanghai",
    "Asia/Ulan_Bator": "Asia/Ulaanbaatar",
    "Asia/Ashkhabad":  "Asia/Ashgabat",
    "Asia/Macao":      "Asia/Macau",
    "Asia/Thimbu":     "Asia/Thimphu",
    "Asia/Ujung_Pandang": "Asia/Makassar",
    "Asia/Tel_Aviv":   "Asia/Jerusalem",
    "Asia/Istanbul":   "Europe/Istanbul",
    "Asia/Kashgar":    "Asia/Urumqi",
    "Europe/Kiev":     "Europe/Kyiv",
    "Europe/Uzhgorod":"Europe/Kyiv",
    "Europe/Zaporozhye": "Europe/Kyiv",
    "Europe/Nicosia":  "Asia/Nicosia",
    "Europe/Belfast":  "Europe/London",
    "Europe/Bratislava": "Europe/Prague",
    "Europe/Ljubljana":"Europe/Belgrade",
    "Europe/Podgorica":"Europe/Belgrade",
    "Europe/Sarajevo": "Europe/Belgrade",
    "Europe/Skopje":   "Europe/Belgrade",
    "Europe/Zagreb":   "Europe/Belgrade",
    "Africa/Asmera":   "Africa/Nairobi",
    "Africa/Timbuktu": "Africa/Abidjan",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Catamarca":    "America/Argentina/Catamarca",
    "America/Cordoba":      "America/Argentina/Cordoba",
    "America/Jujuy":        "America/Argentina/Jujuy",
    "America/Mendoza":      "America/Argentina/Mendoza",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Fort_Wayne":   "America/Indiana/Indianapolis",
    "America/Louisville":   "America/Kentucky/Louisville",
    "America/Montreal":     "America/Toronto",
    "America/Godthab":      "America/Nuuk",
    "America/Atka":         "America/Adak",
    "America/Shiprock":     "America/Denver",
    "America/Virgin":       "America/Puerto_Rico",
    "Pacific/Truk":    "Pacific/Chuuk",
    "Pacific/Ponape":  "Pacific/Pohnpei",
    "Pacific/Yap":     "Pacific/Chuuk",
    "Pacific/Johnston":"Pacific/Honolulu",
    "Pacific/Samoa":   "Pacific/Pago_Pago",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "Australia/ACT":   "Australia/Sydney",
    "Australia/NSW":   "Australia/Sydney",
    "Australia/North": "Australia/Darwin",
    "Australia/Queensland": "Australia/Brisbane",
    "Australia/South": "Australia/Adelaide",
    "Australia/Tasmania":   "Australia/Hobart",
    "Australia/Victoria":   "Australia/Melbourne",
    "Australia/West":  "Australia/Perth",
    "Australia/Canberra":   "Australia/Sydney",
    "US/Eastern":      "America/New_York",
    "US/Central":      "America/Chicago",
    "US/Mountain":     "America/Denver",
    "US/Pacific":      "America/Los_Angeles",
    "US/Alaska":       "America/Anchorage",
    "US/Hawaii":       "Pacific/Honolulu",
    "US/Aleutian":     "America/Adak",
    "US/Arizona":      "America/Phoenix",
    "US/Michigan":     "America/Detroit",
    "US/Samoa":        "Pacific/Pago_Pago",
    "Canada/Atlantic": "America/Halifax",
    "Canada/Central":  "America/Winnipeg",
    "Canada/Eastern":  "America/Toronto",
    "Canada/Mountain": "America/Edmonton",
    "Canada/Pacific":  "America/Vancouver",
    "Canada/Newfoundland": "America/St_Johns",
    "Canada/Saskatchewan":  "America/Regina",
    "Canada/Yukon":    "America/Whitehorse",
    "Mexico/BajaNorte":"America/Tijuana",
    "Mexico/BajaSur":  "America/Mazatlan",
    "Mexico/General":  "America/Mexico_City",
    "Brazil/East":     "America/Sao_Paulo",
    "Brazil/West":     "America/Manaus",
    "Brazil/Acre":     "America/Rio_Branco",
    "Chile/Continental": "America/Santiago",
    "Japan":           "Asia/Tokyo",
    "Egypt":           "Africa/Cairo",
    "GB":              "Europe/London",
    "Iceland":         "Atlantic/Reykjavik",
    "Iran":            "Asia/Tehran",
    "Israel":          "Asia/Jerusalem",
    "Jamaica":         "America/Jamaica",
    "Poland":          "Europe/Warsaw",
    "Portugal":        "Europe/Lisbon",
    "PRC":             "Asia/Shanghai",
    "ROC":             "Asia/Taipei",
    "ROK":             "Asia/Seoul",
    "Singapore":       "Asia/Singapore",
    "Turkey":          "Europe/Istanbul",
    "W-SU":            "Europe/Moscow",
    "NZ":              "Pacific/Auckland",
    "Hongkong":        "Asia/Hong_Kong",
    "Cuba":            "America/Havana",
    "Libya":           "Africa/Tripoli",
}


def get_safe_zoneinfo(tz_name: str | None) -> ZoneInfo:
    """Safely resolve any timezone name to a ZoneInfo, for any location on earth.

    Resolution order:
      1. Try the name directly — handles all valid modern IANA names.
      2. Look it up in _TZ_ALIASES — covers historical/legacy names that are absent
         from minimal Linux system tz databases but still emitted by browsers.
      3. Fall back to UTC — so a stale or unrecognised value is never fatal.
    """
    if not tz_name or not tz_name.strip():
        return ZoneInfo("UTC")

    raw = tz_name.strip()

    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, KeyError, Exception):
        pass

    canonical = _TZ_ALIASES.get(raw)
    if canonical:
        try:
            return ZoneInfo(canonical)
        except (ZoneInfoNotFoundError, KeyError, Exception):
            pass

    return ZoneInfo("UTC")


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        token_type: str = payload.get("type")
        token_ver: int = payload.get("ver", 0)
        if user_id is None or token_type != "access":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    stmt = select(User).where(User.id == uuid.UUID(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # Cross-device logout: reject tokens issued before the latest logout
    if token_ver != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalidated (logged out from another device)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    # Here we could check if user is banned/deleted
    return current_user
