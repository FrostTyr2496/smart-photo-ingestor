"""Location service for resolving GPS coordinates to place names."""

import time
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
import logging

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class LocationInfo:
    """Location information resolved from GPS coordinates."""
    latitude: float
    longitude: float
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    formatted_address: Optional[str] = None
    place_type: Optional[str] = None  # e.g., "park", "beach", "city", "mountain"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'city': self.city,
            'state': self.state,
            'country': self.country,
            'formatted_address': self.formatted_address,
            'place_type': self.place_type
        }
    
    def get_location_context(self) -> str:
        """Get human-readable location context for LLM prompts."""
        if not self.formatted_address and not self.city:
            return f"coordinates {self.latitude:.4f}, {self.longitude:.4f}"
        
        parts = []
        if self.city:
            parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.country:
            parts.append(self.country)
        
        location_str = ", ".join(parts) if parts else self.formatted_address
        
        if self.place_type:
            return f"{location_str} ({self.place_type})"
        
        return location_str or f"coordinates {self.latitude:.4f}, {self.longitude:.4f}"


class LocationService:
    """Service for resolving GPS coordinates to location information."""
    
    def __init__(self, enable_reverse_geocoding: bool = True, cache_size: int = 100):
        """Initialize location service.
        
        Args:
            enable_reverse_geocoding: Whether to perform reverse geocoding
            cache_size: Maximum number of locations to cache
        """
        self.enable_reverse_geocoding = enable_reverse_geocoding
        self.cache_size = cache_size
        self._location_cache: Dict[Tuple[float, float], LocationInfo] = {}
        self._last_request_time = 0
        self._min_request_interval = 1.0  # Minimum seconds between API requests
    
    def get_location_info(self, latitude: float, longitude: float) -> Optional[LocationInfo]:
        """Get location information for GPS coordinates.
        
        Args:
            latitude: GPS latitude
            longitude: GPS longitude
            
        Returns:
            LocationInfo object or None if resolution fails
        """
        # Round coordinates to reduce cache size and API calls
        lat_rounded = round(latitude, 4)
        lon_rounded = round(longitude, 4)
        cache_key = (lat_rounded, lon_rounded)
        
        # Check cache first
        if cache_key in self._location_cache:
            return self._location_cache[cache_key]
        
        # Create basic location info
        location_info = LocationInfo(latitude=latitude, longitude=longitude)
        
        # Perform reverse geocoding if enabled
        if self.enable_reverse_geocoding and REQUESTS_AVAILABLE:
            try:
                resolved_info = self._reverse_geocode(latitude, longitude)
                if resolved_info:
                    location_info = resolved_info
            except Exception as e:
                logger.warning(f"Reverse geocoding failed for {latitude}, {longitude}: {e}")
        
        # Cache the result
        self._cache_location(cache_key, location_info)
        
        return location_info
    
    def _reverse_geocode(self, latitude: float, longitude: float) -> Optional[LocationInfo]:
        """Perform reverse geocoding using OpenStreetMap Nominatim API.
        
        Args:
            latitude: GPS latitude
            longitude: GPS longitude
            
        Returns:
            LocationInfo with resolved address or None if failed
        """
        # Rate limiting
        current_time = time.time()
        if current_time - self._last_request_time < self._min_request_interval:
            time.sleep(self._min_request_interval - (current_time - self._last_request_time))
        
        try:
            # Use OpenStreetMap Nominatim API (free, no API key required)
            url = "https://nominatim.openstreetmap.org/reverse"
            params = {
                'lat': latitude,
                'lon': longitude,
                'format': 'json',
                'addressdetails': 1,
                'zoom': 14,  # City/town level
                'accept-language': 'en'
            }
            
            headers = {
                'User-Agent': 'PhotoIngestTool/1.0 (https://github.com/example/photo-ingest-tool)'
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=10)
            self._last_request_time = time.time()
            
            if response.status_code == 200:
                data = response.json()
                return self._parse_nominatim_response(latitude, longitude, data)
            else:
                logger.warning(f"Nominatim API returned status {response.status_code}")
                
        except Exception as e:
            logger.warning(f"Reverse geocoding request failed: {e}")
        
        return None
    
    def _parse_nominatim_response(self, latitude: float, longitude: float, data: Dict[str, Any]) -> LocationInfo:
        """Parse Nominatim API response into LocationInfo.
        
        Args:
            latitude: Original latitude
            longitude: Original longitude
            data: API response data
            
        Returns:
            LocationInfo object
        """
        address = data.get('address', {})
        
        # Extract location components
        city = (address.get('city') or 
                address.get('town') or 
                address.get('village') or 
                address.get('hamlet'))
        
        state = (address.get('state') or 
                address.get('province') or 
                address.get('region'))
        
        country = address.get('country')
        
        # Determine place type from the response
        place_type = self._determine_place_type(data, address)
        
        # Create formatted address
        formatted_parts = []
        if city:
            formatted_parts.append(city)
        if state:
            formatted_parts.append(state)
        if country:
            formatted_parts.append(country)
        
        formatted_address = ", ".join(formatted_parts) if formatted_parts else data.get('display_name')
        
        return LocationInfo(
            latitude=latitude,
            longitude=longitude,
            city=city,
            state=state,
            country=country,
            formatted_address=formatted_address,
            place_type=place_type
        )
    
    def _determine_place_type(self, data: Dict[str, Any], address: Dict[str, Any]) -> Optional[str]:
        """Determine the type of place from API response.
        
        Args:
            data: Full API response
            address: Address components
            
        Returns:
            Place type string or None
        """
        # Check for specific place types in the response
        category = data.get('category', '')
        place_type = data.get('type', '')
        
        # Map OSM categories to human-readable types
        type_mapping = {
            'natural': {
                'beach': 'beach',
                'peak': 'mountain',
                'forest': 'forest',
                'lake': 'lake',
                'river': 'river',
                'park': 'park'
            },
            'leisure': {
                'park': 'park',
                'nature_reserve': 'nature reserve',
                'beach_resort': 'beach resort'
            },
            'tourism': {
                'attraction': 'tourist attraction',
                'viewpoint': 'scenic viewpoint',
                'national_park': 'national park'
            },
            'place': {
                'city': 'city',
                'town': 'town',
                'village': 'village'
            }
        }
        
        if category in type_mapping and place_type in type_mapping[category]:
            return type_mapping[category][place_type]
        
        # Fallback to basic categorization
        if address.get('tourism'):
            return 'tourist area'
        elif address.get('natural'):
            return 'natural area'
        elif address.get('leisure'):
            return 'recreational area'
        
        return None
    
    def _cache_location(self, cache_key: Tuple[float, float], location_info: LocationInfo):
        """Cache location information.
        
        Args:
            cache_key: Cache key (rounded coordinates)
            location_info: Location information to cache
        """
        # Simple LRU-like cache management
        if len(self._location_cache) >= self.cache_size:
            # Remove oldest entry
            oldest_key = next(iter(self._location_cache))
            del self._location_cache[oldest_key]
        
        self._location_cache[cache_key] = location_info
    
    def clear_cache(self):
        """Clear the location cache."""
        self._location_cache.clear()
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        return {
            'cached_locations': len(self._location_cache),
            'cache_size_limit': self.cache_size
        }