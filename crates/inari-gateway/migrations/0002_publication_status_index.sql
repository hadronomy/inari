CREATE INDEX publications_agent_type_received
    ON publications (agent_id, message_type, received_at DESC);
