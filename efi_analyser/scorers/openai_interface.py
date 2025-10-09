"""
OpenAI interface for making API calls to OpenAI's GPT models.
"""

from __future__ import annotations

import os
import json
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from pathlib import Path

import openai
from dotenv import load_dotenv
import sys
from pathlib import Path

# Add project root to path for cache manager
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
from cache.llm_cache_manager import get_cache_manager

# Load environment variables
load_dotenv()


@dataclass
class OpenAIConfig:
    """Configuration for OpenAI interface."""
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.0
    timeout: float = 60.0
    max_retries: int = 3
    retry_delay: float = 1.0
    cache_dir: Path = Path("cache") / "openai"
    ignore_cache: bool = False
    verbose: bool = False
    
    # Models that only support default temperature (1.0)
    _DEFAULT_TEMP_MODELS = {"gpt-5-nano"}
    
    def __post_init__(self):
        """Validate and adjust temperature based on model capabilities."""
        if self.model in self._DEFAULT_TEMP_MODELS and self.temperature != 1.0:
            if self.verbose:
                print(f"⚠️ Model {self.model} only supports default temperature (1.0), overriding {self.temperature} -> 1.0")
            self.temperature = 1.0
    
    @classmethod
    def get_default_temperature(cls, model: str) -> float:
        """Get the default temperature for a given model."""
        return 1.0 if model in cls._DEFAULT_TEMP_MODELS else 0.0
    
    @classmethod
    def supports_custom_temperature(cls, model: str) -> bool:
        """Check if a model supports custom temperature values."""
        return model not in cls._DEFAULT_TEMP_MODELS


class OpenAIInterface:
    """Interface for making OpenAI API calls with caching and error handling."""

    def __init__(self, name: str = "openai_interface", config: Optional[OpenAIConfig] = None):
        """Initialize OpenAI interface.
        
        Args:
            name: Name for this interface instance
            config: Configuration object
        """
        self.name = name
        self.config = config or OpenAIConfig()
        
        # Ensure cache directory exists
        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        
        self.client = openai.OpenAI(api_key=api_key)
        
        # Initialize cache manager
        self.cache_manager = get_cache_manager()

    def spec_key(self) -> str:
        payload = {
            "name": self.name,
            "model": self.config.model,
            "temperature": self.config.temperature,
            "timeout": self.config.timeout,
        }
        serialized = json.dumps(payload, sort_keys=True)
        import hashlib

        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:12]

    def infer(self, messages: List[Dict[str, str]]) -> str:
        """Send messages to OpenAI and get response.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content'
            
        Returns:
            Raw response string from OpenAI
        """
        return self._inference_with_cache(messages)

    def _inference_with_cache(self, messages: List[Dict[str, str]]) -> str:
        """Get response from OpenAI with centralized caching."""
        # Prepare parameters for cache lookup
        parameters = {
            "temperature": self.config.temperature,
            "timeout": self.config.timeout
        }
        
        # Check cache first
        if not self.config.ignore_cache:
            cached_entry = self.cache_manager.get(self.config.model, messages, parameters)
            if cached_entry is not None:
                if self.config.verbose:
                    print(f"📋 Using cached response for {self.name} ({self.config.model})")
                return cached_entry.response
        
        # Make API call
        if self.config.verbose:
            print(f"🚀 Making OpenAI API call to {self.name} ({self.config.model})...")
        
        start_time = time.time()
        response = self._safe_infer(messages)
        inference_time = time.time() - start_time
        
        if self.config.verbose:
            print(f"✅ OpenAI API call completed in {inference_time:.2f}s for {self.name} ({self.config.model})")
        
        # Cache the response using centralized cache manager
        self.cache_manager.put(
            model=self.config.model,
            messages=messages,
            parameters=parameters,
            response=response,
            inference_time=inference_time
        )
        
        return response

    def _safe_infer(self, messages: List[Dict[str, str]]) -> str:
        """Make OpenAI API call with retry logic and error handling."""
        for attempt in range(self.config.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=self.config.temperature,
                    timeout=self.config.timeout
                )
                
                return response.choices[0].message.content
                
            except Exception as e:
                if attempt < self.config.max_retries - 1:
                    if self.config.verbose:
                        print(f"⚠️ OpenAI API call failed (attempt {attempt + 1}/{self.config.max_retries}): {e}")
                    time.sleep(self.config.retry_delay * (2 ** attempt))  # Exponential backoff
                else:
                    # Last attempt failed, return error response
                    error_msg = f"OpenAI API error after {self.config.max_retries} attempts: {e}"
                    if self.config.verbose:
                        print(f"❌ {error_msg}")
                    return json.dumps({"error": error_msg, "score": 0.0, "rationale": "API call failed"})

    def _make_cache_key(self, messages: List[Dict[str, str]]) -> str:
        """Generate cache key for messages."""
        import hashlib
        payload = json.dumps({
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get cache file path for given key."""
        return self.config.cache_dir / f"{cache_key}.json"
