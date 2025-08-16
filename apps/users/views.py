from rest_framework import viewsets, permissions, status, filters as drf_filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django_filters import rest_framework as filters

from .serializers import UserSerializer, UserListSerializer, UserCreateSerializer

User = get_user_model()


class UserFilter(filters.FilterSet):
    search = filters.CharFilter(method='search_filter')
    is_active = filters.BooleanFilter()
    is_customer = filters.BooleanFilter()
    is_vendor = filters.BooleanFilter()

    class Meta:
        model = User
        fields = ['search', 'is_active', 'is_customer', 'is_vendor']

    def search_filter(self, queryset, name, value):
        return queryset.filter(
            Q(username__icontains=value) |
            Q(email__icontains=value) |
            Q(first_name__icontains=value) |
            Q(last_name__icontains=value)
        )


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (permissions.IsAuthenticated,)
    filterset_class = UserFilter
    filter_backends = (filters.DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter)
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering_fields = ['username', 'email', 'created_at', 'updated_at']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return UserListSerializer
        elif self.action == 'create':
            return UserCreateSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'toggle_status']:
            return [permissions.IsAdminUser()]
        return [permission() for permission in self.permission_classes]

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError as e:
            return Response({'error': e.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            return Response({'error': 'Something went wrong while creating user.'},
                            status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def active_users(self, request):
        active_users = User.objects.filter(is_active=True)
        serializer = UserListSerializer(active_users, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def user_stats(self, request):
        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        customers = User.objects.filter(is_customer=True).count()
        vendors = User.objects.filter(is_vendor=True).count()
        return Response({
            'total_users': total_users,
            'active_users': active_users,
            'customers': customers,
            'vendors': vendors,
            'inactive_users': total_users - active_users
        })

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAdminUser])
    def toggle_status(self, request, pk=None):
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()
        return Response({'status': 'success', 'is_active': user.is_active})

    @action(detail=False, methods=['get'])
    def customers(self, request):
        customers = User.objects.filter(is_customer=True)
        serializer = UserListSerializer(customers, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def vendors(self, request):
        vendors = User.objects.filter(is_vendor=True)
        serializer = UserListSerializer(vendors, many=True)
        return Response(serializer.data)
