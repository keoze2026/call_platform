class TwiMLBuilder:
    """
    Converts an IVR flow to TwiML markup.
    This is the ONLY file that needs to change if you switch providers.
    Telnyx, Vonage, or any other provider — only change this file.
    """

    @staticmethod
    def build_node(node, base_url: str) -> str:
        node_type = node.node_type
        config = node.config

        if node_type == 'say':
            voice = config.get('voice', 'alice')
            language = config.get('language', 'en-US')
            message = config.get('message', '')
            return f'<Say voice="{voice}" language="{language}">{message}</Say>'

        elif node_type == 'gather':
            timeout = config.get('timeout', 10)
            num_digits = config.get('num_digits', 1)
            message = config.get('message', '')
            voice = config.get('voice', 'alice')
            action_url = f"{base_url}/api/ivr/webhook/gather/{node.flow_id}/{node.id}/"
            inner = f'<Say voice="{voice}">{message}</Say>' if message else ''
            return f'<Gather numDigits="{num_digits}" timeout="{timeout}" action="{action_url}" method="POST">{inner}</Gather>'

        elif node_type == 'dial':
            destination = config.get('destination', '')
            timeout = config.get('timeout', 30)
            return f'<Dial timeout="{timeout}"><Number>{destination}</Number></Dial>'

        elif node_type == 'transfer':
            destination = config.get('destination', '')
            return f'<Dial><Number>{destination}</Number></Dial>'

        elif node_type == 'hangup':
            return '<Hangup/>'

        elif node_type == 'voicemail':
            max_length = config.get('max_length', 300)
            message = config.get('message', 'Please leave a message after the beep.')
            action_url = f"{base_url}/api/ivr/webhook/recording/{node.flow_id}/{node.id}/"
            return f'<Say>{message}</Say><Record maxLength="{max_length}" action="{action_url}" method="POST"/>'

        elif node_type == 'record':
            max_length = config.get('max_length', 300)
            action_url = f"{base_url}/api/ivr/webhook/recording/{node.flow_id}/{node.id}/"
            return f'<Record maxLength="{max_length}" action="{action_url}" method="POST"/>'

        elif node_type == 'pause':
            length = config.get('pause_length', 1)
            return f'<Pause length="{length}"/>'

        return ''

    @staticmethod
    def build_flow(flow, node, base_url: str) -> str:
        twiml = '<?xml version="1.0" encoding="UTF-8"?><Response>'
        twiml += TwiMLBuilder.build_node(node, base_url)
        twiml += '</Response>'
        return twiml