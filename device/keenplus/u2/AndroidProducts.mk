# AndroidProducts.mk -- lunch targets for the KeenPlus U2 port.

PRODUCT_MAKEFILES := \
    $(LOCAL_DIR)/u2.mk

COMMON_LUNCH_CHOICES := \
    u2-userdebug \
    u2-user \
    u2-eng
