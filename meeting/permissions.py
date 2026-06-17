from rest_framework.permissions import BasePermission


class IsClerk(BasePermission):
    """
    Allows access only to users with Clerk role.
    Clerk: Global Admin / Full Database CRUD
    """
    message = "Only Clerks have permission to perform this action."
    
    def has_permission(self, request, view):
        return bool(request.user and hasattr(request.user, 'profile') and 
                   request.user.profile.role == 'clerk')


class IsRecordingClerk(BasePermission):
    """
    Allows access only to users with Recording Clerk role.
    Recording Clerk: Write & Update on specific endpoints
    Can save Meeting Minutes and Hymnal items.
    """
    message = "Only Recording Clerks have permission to perform this action."
    
    def has_permission(self, request, view):
        return bool(request.user and hasattr(request.user, 'profile') and 
                   request.user.profile.role == 'recording_clerk')


class IsMember(BasePermission):
    """
    Allows access only to authenticated members.
    Member: Authenticated Read & Write Socket channels
    Accesses streaming content, reads archives, posts live chat.
    """
    message = "Only Members have permission to perform this action."
    
    def has_permission(self, request, view):
        return bool(request.user and hasattr(request.user, 'profile') and 
                   request.user.profile.role in ['member', 'recording_clerk', 'clerk'])


class IsAttender(BasePermission):
    """
    Allows access only to attenders (can be unauthenticated).
    Attender: Unauthenticated Read Only
    Public access to worship notifications, bible reader, public items.
    """
    message = "Attenders have limited read-only access."
    
    def has_permission(self, request, view):
        # Allow GET requests for attenders
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        # Authenticated users can do more
        return bool(request.user and request.user.is_authenticated)


class IsClerkOrRecordingClerk(BasePermission):
    """
    Allows access to Clerks or Recording Clerks.
    """
    message = "Only leadership roles have permission to perform this action."
    
    def has_permission(self, request, view):
        return bool(request.user and hasattr(request.user, 'profile') and 
                   request.user.profile.role in ['clerk', 'recording_clerk'])


class IsOwnerOrClerk(BasePermission):
    """
    Allows access if user is the owner of the object or a Clerk.
    """
    message = "You do not have permission to perform this action."
    
    def has_object_permission(self, request, view, obj):
        if hasattr(request.user, 'profile') and request.user.profile.role == 'clerk':
            return True
        if hasattr(obj, 'created_by'):
            return obj.created_by == request.user
        return False
