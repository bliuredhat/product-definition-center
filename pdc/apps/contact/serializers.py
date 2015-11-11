#
# Copyright (c) 2015 Red Hat
# Licensed under The MIT License (MIT)
# http://opensource.org/licenses/MIT
#
import json

from django.contrib.contenttypes.models import ContentType
from django.utils.translation import ugettext_lazy as _
from rest_framework import serializers

from pdc.apps.common.serializers import DynamicFieldsSerializerMixin, StrictSerializerMixin
from pdc.apps.common.fields import ChoiceSlugField
from .models import ContactRole, Person, Maillist, Contact, RoleContact


class LimitField(serializers.IntegerField):
    UNLIMITED_STR = 'unlimited'
    doc_format = '"{}"|int'.format(UNLIMITED_STR)

    def __init__(self, unlimited_value, **kwargs):
        kwargs['min_value'] = 0
        super(LimitField, self).__init__(**kwargs)
        self.unlimited_value = unlimited_value

    def to_representation(self, obj):
        if obj == self.unlimited_value:
            return self.__class__.UNLIMITED_STR
        return super(LimitField, self).to_representation(obj)

    def to_internal_value(self, value):
        if value == self.__class__.UNLIMITED_STR:
            return self.unlimited_value
        return super(LimitField, self).to_internal_value(value)


class ContactRoleSerializer(StrictSerializerMixin,
                            serializers.HyperlinkedModelSerializer):
    name = serializers.SlugField()
    count_limit = LimitField(required=False, unlimited_value=ContactRole.UNLIMITED)

    class Meta:
        model = ContactRole
        fields = ('name', 'count_limit')


class PersonSerializer(DynamicFieldsSerializerMixin,
                       StrictSerializerMixin,
                       serializers.HyperlinkedModelSerializer):

    class Meta:
        model = Person
        fields = ('id', 'username', 'email')


class MaillistSerializer(DynamicFieldsSerializerMixin,
                         StrictSerializerMixin,
                         serializers.HyperlinkedModelSerializer):

    class Meta:
        model = Maillist
        fields = ('id', 'mail_name', 'email')


class ContactField(serializers.DictField):
    doc_format = '{"id": "int", "email": "email address", "username|mail_name": "string"}'
    writable_doc_format = '{"email": "email address", "username|mail_name": "string"}'

    child = serializers.CharField()
    field_to_class = {
        "username": Person,
        "mail_name": Maillist,
    }
    class_to_serializer = {
        "Person": PersonSerializer,
        "Maillist": MaillistSerializer,
    }

    def to_representation(self, value):
        leaf_value = value.as_leaf_class()
        serializer_cls = self.class_to_serializer.get(
            type(leaf_value).__name__, None)
        if serializer_cls:
            leaf_serializer = serializer_cls(exclude_fields=['url'],
                                             context=self.context)
            return leaf_serializer.to_representation(leaf_value)
        else:
            raise serializers.ValidationError("Unsupported Contact: %s" % value)

    def to_internal_value(self, data):
        v_data = super(ContactField, self).to_internal_value(data)
        for key, clazz in self.field_to_class.items():
            if key in v_data:
                contact, created = clazz.objects.get_or_create(**v_data)
                if created:
                    request = self.context.get('request', None)
                    model_name = ContentType.objects.get_for_model(contact).model
                    if request:
                        request.changeset.add(model_name,
                                              contact.id,
                                              'null',
                                              json.dumps(contact.export()))
                return contact
        raise serializers.ValidationError('Could not determine type of contact.')


class UniqueRoleContactValidator(object):
    message = _('The fields {field_names} must make a unique set.')

    def __init__(self):
        self.query_set = RoleContact.objects.all()
        self.unique_together = RoleContact._meta.unique_together[0]

    def set_context(self, data):
        self.instance = data.instance

    def get_contact_role_pk(self, instance):
        crole_instance = ContactRole.objects.get(name=instance.name)
        return crole_instance.pk

    def get_contact_pk(self, instance):
        data = {}
        leaf_instance = instance.as_leaf_class()
        query_prefix = ''
        if isinstance(leaf_instance, Person):
            query_prefix = 'person__'
        elif isinstance(leaf_instance, Maillist):
            query_prefix = 'maillist__'
        else:
            raise serializers.ValidationError("Unsupported Contact: %s" % instance)
        for field in leaf_instance._meta.fields:
            if field.primary_key:
                continue
            data[query_prefix + field.name] = getattr(leaf_instance, field.name)
        contact = Contact.objects.get(**data)
        return contact.pk

    def filter_queryset(self, **kwargs):
        queryset = self.query_set.filter(**kwargs)

        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(self.message.format(field_names=self.unique_together))

    def __call__(self, value):
        if self.instance:
            crole_input = value.get("contact_role", self.instance.contact_role)
            contact_input = value.get("contact", self.instance.contact)
        else:
            crole_input = value.get("contact_role")
            contact_input = value.get("contact")
        self.filter_queryset(**{
            "contact_role_id": self.get_contact_role_pk(crole_input),
            "contact_id": self.get_contact_pk(contact_input)
        })


class RoleContactSerializer(DynamicFieldsSerializerMixin,
                            StrictSerializerMixin,
                            serializers.HyperlinkedModelSerializer):
    contact_role = ChoiceSlugField(queryset=ContactRole.objects.all(), slug_field='name')
    contact = ContactField()

    class Meta:
        model = RoleContact
        fields = ('id', 'contact_role', 'contact')
        validators = [
            UniqueRoleContactValidator()
        ]