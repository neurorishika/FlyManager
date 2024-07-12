import lazy_loader


__getattr__, __dir__, __all__ = lazy_loader.attach(
    __name__,
    submodules={
        'labels',
        'rdp_client',
    },
    submod_attrs={
        'labels': [
            'AveryLabel',
            'BUSINESS_CARDS',
            'RETURN_ADDRESS',
            'labelInfo',
        ],
        'rdp_client': [
            'unlock_and_unzip_file',
            'zip_and_lock_folder',
        ],
    },
)

__all__ = ['AveryLabel', 'BUSINESS_CARDS', 'RETURN_ADDRESS', 'labelInfo',
           'labels', 'rdp_client', 'unlock_and_unzip_file',
           'zip_and_lock_folder']
