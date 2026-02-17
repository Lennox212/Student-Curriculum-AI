"""
Custom widgets for Django admin.
Provides a PrettyJSONWidget for better JSON field editing.
"""

import json
from django import forms
from django.utils.safestring import mark_safe


class PrettyJSONWidget(forms.Textarea):
    """
    A custom Textarea widget that provides:
    - Auto-formatted JSON with proper indentation
    - Monospace font for readability
    - Syntax highlighting (via CSS classes applied by JS)
    - JSON validation before form submission
    - Line count display
    """

    def __init__(self, attrs=None):
        default_attrs = {
            'class': 'pretty-json-widget',
            'rows': 12,
            'cols': 80,
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    def format_value(self, value):
        """Format the JSON value with proper indentation."""
        if value is None:
            return '{}'

        # If it's already a string, try to parse and re-format it
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                return value

        # If it's a dict or list, format it
        try:
            return json.dumps(value, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    def render(self, name, value, attrs=None, renderer=None):
        # Format the value
        formatted_value = self.format_value(value)

        # Render the base textarea
        textarea_html = super().render(name, formatted_value, attrs, renderer)

        # Add our custom CSS and JS
        widget_html = f'''
        <style>
            .pretty-json-widget {{
                font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                line-height: 1.5;
                padding: 12px;
                border: 1px solid #ccc;
                border-radius: 4px;
                background-color: #1e1e1e;
                color: #d4d4d4;
                tab-size: 2;
                resize: vertical;
                width: 100%;
                min-height: 150px;
            }}
            
            .pretty-json-widget:focus {{
                outline: none;
                border-color: #417690;
                background-color: #252526;
                color: #d4d4d4;
                box-shadow: 0 0 3px rgba(65, 118, 144, 0.3);
            }}
            
            .pretty-json-widget.invalid {{
                border-color: #f44336;
                background-color: #2d1f1f;
            }}
            
            .pretty-json-widget.valid {{
                border-color: #4caf50;
            }}
            
            .json-widget-status {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 5px;
                font-size: 12px;
                color: #666;
            }}
            
            .json-widget-status .status-valid {{
                color: #28a745;
            }}
            
            .json-widget-status .status-invalid {{
                color: #ba2121;
            }}
            
            .json-widget-status .line-count {{
                color: #666;
            }}
            
            .json-widget-container {{
                max-width: 800px;
            }}
            
            .json-format-btn {{
                padding: 4px 10px;
                font-size: 11px;
                background: #417690;
                color: white;
                border: none;
                border-radius: 3px;
                cursor: pointer;
                margin-left: 10px;
            }}
            
            .json-format-btn:hover {{
                background: #205067;
            }}
        </style>
        
        <div class="json-widget-container">
            {textarea_html}
            <div class="json-widget-status" id="status_{name}">
                <span class="validation-message"></span>
                <span>
                    <span class="line-count"></span>
                    <button type="button" class="json-format-btn" onclick="formatJSON_{name.replace('-', '_')}()">Format</button>
                </span>
            </div>
        </div>
        
        <script>
            (function() {{
                const textarea = document.querySelector('textarea[name="{name}"]');
                const statusDiv = document.getElementById('status_{name}');
                const validationMsg = statusDiv.querySelector('.validation-message');
                const lineCount = statusDiv.querySelector('.line-count');
                
                function validateJSON() {{
                    const value = textarea.value.trim();
                    
                    if (!value) {{
                        textarea.classList.remove('valid', 'invalid');
                        validationMsg.textContent = '';
                        validationMsg.className = 'validation-message';
                        return true;
                    }}
                    
                    try {{
                        JSON.parse(value);
                        textarea.classList.remove('invalid');
                        textarea.classList.add('valid');
                        validationMsg.textContent = '✓ Valid JSON';
                        validationMsg.className = 'validation-message status-valid';
                        return true;
                    }} catch (e) {{
                        textarea.classList.remove('valid');
                        textarea.classList.add('invalid');
                        validationMsg.textContent = '✗ Invalid: ' + e.message;
                        validationMsg.className = 'validation-message status-invalid';
                        return false;
                    }}
                }}
                
                function updateLineCount() {{
                    const lines = textarea.value.split('\\n').length;
                    lineCount.textContent = lines + ' line' + (lines !== 1 ? 's' : '');
                }}
                
                // Make format function globally accessible for the button
                window['formatJSON_{name.replace('-', '_')}'] = function() {{
                    try {{
                        const parsed = JSON.parse(textarea.value);
                        textarea.value = JSON.stringify(parsed, null, 2);
                        validateJSON();
                        updateLineCount();
                    }} catch (e) {{
                        // If invalid, just validate to show error
                        validateJSON();
                    }}
                }};
                
                // Initial validation and line count
                validateJSON();
                updateLineCount();
                
                // Validate on input
                textarea.addEventListener('input', function() {{
                    validateJSON();
                    updateLineCount();
                }});
                
                // Validate and prevent form submission if invalid
                textarea.closest('form').addEventListener('submit', function(e) {{
                    if (!validateJSON()) {{
                        e.preventDefault();
                        textarea.focus();
                        alert('Please fix the JSON errors before saving.');
                    }}
                }});
            }})();
        </script>
        '''

        return mark_safe(widget_html)


class ReadOnlyJSONWidget(forms.Widget):
    """
    A read-only widget that displays JSON in a formatted,
    syntax-highlighted view (no editing).
    """

    def render(self, name, value, attrs=None, renderer=None):
        if value is None:
            formatted = '{}'
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
                formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                formatted = value
        else:
            try:
                formatted = json.dumps(value, indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                formatted = str(value)

        # Escape HTML entities
        import html
        escaped = html.escape(formatted)

        html_output = f'''
        <style>
            .readonly-json {{
                font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                line-height: 1.5;
                padding: 12px;
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: #f8f8f8;
                white-space: pre;
                overflow-x: auto;
                max-width: 800px;
            }}
        </style>
        <pre class="readonly-json">{escaped}</pre>
        <input type="hidden" name="{name}" value="{html.escape(formatted)}">
        '''

        return mark_safe(html_output)