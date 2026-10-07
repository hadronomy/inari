def migrate(cr, _version):
    cr.execute("""
        UPDATE inari_device AS device
           SET controller_uuid = agent.agent_id || '/' || device.device_id
          FROM inari_agent AS agent
         WHERE device.agent_id = agent.id
    """)
