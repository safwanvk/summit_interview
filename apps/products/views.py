from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q, Avg
from django_filters import rest_framework as filters

from .models import Category, Product, ProductReview, ProductImage
from .serializers import (
    CategorySerializer, ProductSerializer, ProductListSerializer,
    ProductCreateSerializer, ProductReviewSerializer, ProductImageSerializer
)


class CategoryFilter(filters.FilterSet):
    """
    Filter for Category model.
    """
    search = filters.CharFilter(method='search_filter')
    is_active = filters.BooleanFilter()
    
    class Meta:
        model = Category
        fields = ['search', 'is_active']
    
    def search_filter(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value)
        )


class ProductFilter(filters.FilterSet):
    """
    Filter for Product model.
    """
    search = filters.CharFilter(method='search_filter')
    min_price = filters.NumberFilter(field_name='price', lookup_expr='gte')
    max_price = filters.NumberFilter(field_name='price', lookup_expr='lte')
    category = filters.NumberFilter(field_name='category__id')
    vendor = filters.NumberFilter(field_name='vendor__id')
    in_stock = filters.BooleanFilter(method='filter_in_stock')
    
    class Meta:
        model = Product
        fields = ['search', 'min_price', 'max_price', 'category', 'vendor', 'in_stock', 'is_active']
    
    def search_filter(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) |
            Q(description__icontains=value) |
            Q(sku__icontains=value)
        )
    
    def filter_in_stock(self, queryset, name, value):
        if value:
            return queryset.filter(stock_quantity__gt=0)
        return queryset


class CategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Category model.
    """
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_class = CategoryFilter
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return super().get_permissions()
    
    def get_queryset(self):
        return super().get_queryset()
    
    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        data['slug'] = data.get('name', '').lower().replace(' ', '-')
        request.data = data
        return super().create(request, *args, **kwargs)
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """
        Get all active categories.
        """
        categories = Category.objects.filter(is_active=True)
        serializer = self.get_serializer(categories, many=True)
        return Response(serializer.data)


class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Product model.
    """
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_class = ProductFilter
    search_fields = ['name', 'description', 'sku']
    ordering_fields = ['name', 'price', 'created_at', 'stock_quantity']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ProductListSerializer
        elif self.action == 'create':
            return ProductCreateSerializer
        return ProductSerializer
    
    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated()]
        return super().get_permissions()
    
    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == 'list':
            return queryset
        return queryset.select_related('category', 'vendor').prefetch_related('images', 'reviews')
    
    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=False, methods=['get'])
    def in_stock(self, request):
        """
        Get all products in stock.
        """
        products = Product.objects.all()
        in_stock_products = [product for product in products if product.stock_quantity > 0]
        
        serializer = ProductListSerializer(in_stock_products, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def add_review(self, request, pk=None):
        """
        Add a review to a product.
        """
        product = self.get_object()
        serializer = ProductReviewSerializer(data=request.data)
        
        if serializer.is_valid():
            serializer.save(product=product, user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def top_rated(self, request):
        """
        Get top rated products.
        """
        products = Product.objects.all()
        top_rated = []
        
        for product in products:
            reviews = product.reviews.all()
            if reviews:
                avg_rating = sum(review.rating for review in reviews) / len(reviews)
                if avg_rating >= 4.0:
                    top_rated.append(product)
        
        serializer = ProductListSerializer(top_rated, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def update_stock(self, request, pk=None):
        """
        Update product stock quantity.
        """
        product = self.get_object()
        quantity = request.data.get('quantity')
        
        if quantity is not None:
            product.stock_quantity = quantity
            product.save()
        
        serializer = ProductSerializer(product)
        return Response(serializer.data)


class ProductReviewViewSet(viewsets.ModelViewSet):
    """
    ViewSet for ProductReview model.
    """
    queryset = ProductReview.objects.all()
    serializer_class = ProductReviewSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated()]
        return super().get_permissions()
    
    def get_queryset(self):
        return super().get_queryset().select_related('user', 'product')
    
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)