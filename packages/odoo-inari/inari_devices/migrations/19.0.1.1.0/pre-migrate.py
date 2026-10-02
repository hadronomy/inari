def migrate(cr, version):
    cr.execute(
        "UPDATE inari_device_capability SET operation = 'open_cash_drawer' "
        "WHERE operation = 'open_cashbox'"
    )
