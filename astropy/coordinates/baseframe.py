# -*- coding: utf-8 -*-
# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
Framework and base classes for coordinate frames/"low-level" coordinate classes.
"""
from __future__ import (absolute_import, unicode_literals, division,
                        print_function)

# Standard library
import inspect
import warnings
from copy import deepcopy

# Dependencies

# Project
from ..extern import six
from ..utils.exceptions import AstropyDeprecationWarning
from .. import units as u
from ..utils import OrderedDict
from .transformations import TransformGraph
from .representation import (BaseRepresentation, CartesianRepresentation,
                             SphericalRepresentation, UnitSphericalRepresentation,
                             REPRESENTATION_CLASSES)

__all__ = ['BaseCoordinateFrame', 'frame_transform_graph', 'GenericFrame']

# the graph used for all transformations between frames
frame_transform_graph = TransformGraph()


def _get_repr_cls(value):
    """Return a valid representation class from ``value`` or raise exception."""
    if value in REPRESENTATION_CLASSES:
        value = REPRESENTATION_CLASSES[value]
    try:
        issubclass(value, BaseRepresentation)  # value might not be a class, so use try
    except:
        raise ValueError('representation is {0!r} but must be a BaseRepresentation class '
                         ' or one of the string aliases {1}'
                         .format(value, list(REPRESENTATION_CLASSES)))
    return value


class FrameMeta(type):
    def __new__(cls, name, parents, clsdct):
        # somewhat hacky, but this is the best way to get the MRO according to
        # https://mail.python.org/pipermail/python-list/2002-December/167861.html
        mro = super(FrameMeta, cls).__new__(cls, name, parents, clsdct).__mro__
        parent_clsdcts = [c.__dict__ for c in mro]
        parent_clsdcts.insert(0, clsdct)

        #now look through the whole MRO for the relevant class attributes
        frame_attrs = {}
        found_pref_repr = found_frame_attrs = False
        for clsdcti in parent_clsdcts:
            if not found_pref_repr and '_representation' in clsdcti:
                found_pref_repr = True
            if not found_frame_attrs and 'frame_attr_names' in clsdcti:
                frame_attrs = clsdcti['frame_attr_names']
                found_frame_attrs = True

            if found_pref_repr and found_frame_attrs:
                break
        else:
            raise ValueError('Could not find the expected BaseCoordinateFrame '
                             'class attributes.  Are you mis-using FrameMeta?')

        # Make properties for the frame_attr_names to make them immutable
        # after creation.
        for attrnm in frame_attrs:
            if attrnm in clsdct:
                if isinstance(clsdct[attrnm], property):
                    #means the property was defined already... so trust the
                    #subclasser and don't make a new one
                    continue
                else:
                    raise ValueError("A non-property exists that's *also* in frame_attr_names")
            clsdct[attrnm] = property(FrameMeta.frame_attr_factory(attrnm))

        # now set the name as lower-case class name, if it isn't explicit
        if 'name' not in clsdct:
            clsdct['name'] = name.lower()

        return super(FrameMeta, cls).__new__(cls, name, parents, clsdct)

    @staticmethod
    def frame_attr_factory(attrnm):
        def getter(self):
            return getattr(self, '_' + attrnm)
        return getter


@six.add_metaclass(FrameMeta)
class BaseCoordinateFrame(object):
    """
    The base class for coordinate frames.

    This class is intended to be subclassed to create instances of specific
    systems.  Subclasses can implement the following attributes.

    * `representation`
        A subclass of `~astropy.coordinates.BaseRepresentation` that will be
        treated as the "standard" representation of this frame, or None to have
        no special representation.

    * `frame_attr_names`
        A dictionary with keys that are the additional attributes necessary to
        specify the frame, and values that are the default values of those
        attributes.

    * `time_attr_names`
        A sequence of attribute names that must be `~astropy.time.Time` objects.
        When given as keywords in the initializer, these will be converted if
        possible (e.g. from the string 'J2000' to the appropriate
        `~astropy.time.Time` object).  Defaults to ``('equinox', 'obstime')``.

    """

    @property
    def representation(self):
        return self._representation

    @representation.setter
    def representation(self, value):
        self._representation = _get_repr_cls(value)

    @classmethod
    def _get_representation_attrs(cls):
        # This exists as a class method only to support handling frame inputs
        # without units, which are deprecated and will be removed.  This can be
        # moved into the property at that time.
        repr_attrs = {}
        for name, repr_cls in REPRESENTATION_CLASSES.items():
            if hasattr(repr_cls, '_default_names') and hasattr(repr_cls, '_default_units'):
                repr_attrs[name] = {'names': repr_cls._default_names,
                                    'units': repr_cls._default_units}

        # Override defaults as needed for this frame
        repr_attrs.update(getattr(cls, '_representation_attrs', {}))

        # Use the class, not the name for the key in representation_attrs
        repr_attrs = dict((REPRESENTATION_CLASSES[name], attrs)
                          for name, attrs in repr_attrs.items())

        return deepcopy(repr_attrs)  # Don't let upstream mess with frame internals

    @property
    def representation_attrs(self):
        return self._get_representation_attrs()

    @property
    def representation_names(self):
        data_names = self.representation._attr_classes.keys()
        repr_names = self.representation_attrs[self.representation]['names']
        out = OrderedDict()
        for repr_name, data_name in zip(repr_names, data_names):
            out[repr_name] = data_name
        return out

    @property
    def representation_units(self):
        repr_attrs = self.representation_attrs[self.representation]
        repr_names = repr_attrs['names']
        repr_units = repr_attrs['units']
        out = OrderedDict()
        for repr_name, repr_unit in zip(repr_names, repr_units):
            if repr_unit:
                out[repr_name] = repr_unit
        return out

    _representation = None

    frame_attr_names = {}  # maps attribute to default value
    time_attr_names = ('equinox', 'obstime')  # Attributes that must be Time objects

    # This __new__ provides for backward-compatibility with pre-0.4 API.
    # TODO: remove in 1.0
    def __new__(cls, *args, **kwargs):

        # Only do backward-compatibility if frame is previously defined one
        frame_name = cls.__name__.lower()
        if frame_name not in ['altaz', 'fk4', 'fk4noeterms', 'fk5', 'galactic', 'icrs']:
            return super(BaseCoordinateFrame, cls).__new__(cls)

        use_skycoord = False

        if len(args) > 1 or (len(args) == 1 and not isinstance(args[0], BaseRepresentation)):
            for arg in args:
                if (not isinstance(arg, u.Quantity)
                    and not isinstance(arg, BaseRepresentation)):
                    msg = ('Initializing frame classes like "{0}" using string '
                           'or other non-Quantity arguments is deprecated, and '
                           'will be removed in the next version of Astropy.  '
                           'Instead, you probably want to use the SkyCoord '
                           'class with the "system={1}" keyword, or if you '
                           'really want to use the low-level frame classes, '
                           'create it with an Angle or Quantity.')

                    warnings.warn(msg.format(cls.__name__,
                                             cls.__name__.lower()),
                                  AstropyDeprecationWarning)
                    use_skycoord = True
                    break

        if 'unit' in kwargs and not use_skycoord:
            warnings.warn("Initializing frames using the ``unit`` argument is "
                          "now deprecated. Use SkyCoord or pass Quantity "
                          " instances to frames instead.", AstropyDeprecationWarning)
            use_skycoord = True

        if not use_skycoord:
            representation = _get_repr_cls(kwargs.get('representation') or cls._representation)
            for key in cls._get_representation_attrs()[representation]['names']:
                if key in kwargs:
                    if not isinstance(kwargs[key], u.Quantity):
                        warnings.warn("Initializing frames using non-Quantity "
                                      "arguments is now deprecated. Use "
                                      "SkyCoord or pass Quantity instances "
                                      "instead.", AstropyDeprecationWarning)
                        use_skycoord = True
                        break

        if use_skycoord:
            kwargs['frame'] = frame_name
            from .sky_coordinate import SkyCoord
            return SkyCoord(*args, **kwargs)
        else:
            return super(BaseCoordinateFrame, cls).__new__(cls)

    def __init__(self, *args, **kwargs):
        self._attr_names_with_defaults = []

        if 'representation' in kwargs:
            self.representation = kwargs.pop('representation')

        representation = None  # if not set below, this is a frame with no data

        for fnm, fdefault in self.frame_attr_names.items():
            # read-only properties for these attributes are made in the
            # metaclass  so we set the 'real' attrbiutes as the name prefaced
            # with an underscore

            if fnm in kwargs:
                value = kwargs.pop(fnm)
                # If attribute is a time (equinox, obstime) then validate and force
                # into a Time object by running through the Time constructor
                if fnm in self.time_attr_names:
                    value = _convert_to_time(fnm, value)
                setattr(self, '_' + fnm, value)
            else:
                setattr(self, '_' + fnm, fdefault)
                self._attr_names_with_defaults.append(fnm)

        pref_rep = self.representation
        args = list(args)  # need to be able to pop them
        if (len(args) > 0) and (isinstance(args[0], BaseRepresentation) or
                                args[0] is None):
            representation = args.pop(0)
            if len(args) > 0:
                raise TypeError('Cannot create a frame with both a '
                                'representation and other positional arguments')

        elif pref_rep:
            pref_kwargs = {}
            for nmkw, nmrep in self.representation_names.items():
                if len(args) > 0:
                    #first gather up positional args
                    pref_kwargs[nmrep] = args.pop(0)
                elif nmkw in kwargs:
                    pref_kwargs[nmrep] = kwargs.pop(nmkw)

            #special-case the Spherical->UnitSpherical if no `distance`
            #TODO: possibly generalize this somehow?

            if pref_kwargs:
                if pref_kwargs.get('distance', True) is None:
                    del pref_kwargs['distance']
                if (pref_rep == SphericalRepresentation and
                        'distance' not in pref_kwargs):
                    representation = UnitSphericalRepresentation(**pref_kwargs)
                else:
                    representation = pref_rep(**pref_kwargs)

        if len(args) > 0:
            raise TypeError(self.__class__.__name__ + '.__init__ had {0} '
                            'remaining unprocessed arguments'.format(len(args)))
        if kwargs:
            raise TypeError('Coordinate frame got unexpected keywords: ' +
                            str(kwargs.keys()))

        self._data = representation

        if self._data:
            self._rep_cache = dict()
            self._rep_cache[representation.__class__.__name__, False] = representation

    @property
    def data(self):
        """
        The coordinate data for this object.  If this frame has no data, an
        `~.exceptions.AttributeError` will be raised.  Use `has_data` to
        check if data is present on this frame object.
        """
        if self._data is None:
            raise AttributeError('The frame object "{0}" does not have '
                                 'associated data'.format(repr(self)))
        return self._data

    @property
    def has_data(self):
        """
        True if this frame has `data`, False otherwise.
        """
        return self._data is not None

    def __len__(self):
        return len(self.data)

    def __nonzero__(self):  # Py 2.x
        return self.isscalar or len(self) != 0

    def __bool__(self):  # Py 3.x
        return self.isscalar or len(self) != 0

    @property
    def shape(self):
        return self.data.shape

    @property
    def isscalar(self):
        return self.data.isscalar

    def realize_frame(self, representation):
        """
        Generates a new frame *with new data* from another frame (which may or
        may not have data).

        Parameters
        ----------
        representation : BaseRepresentation
            The representation to use as the data for the new frame.

        Returns
        -------
        frameobj : same as this frame
            A new object with the same frame attributes as this one, but
            with the ``representation`` as the data.
        """
        frattrs = dict([(nm, getattr(self, nm)) for nm in self.frame_attr_names
                        if nm not in self._attr_names_with_defaults])
        return self.__class__(representation, **frattrs)

    def represent_as(self, new_representation, in_frame_units=False):
        """
        Generate and return a new representation of this frame's `data`.

        Parameters
        ----------
        new_representation : subclass of BaseRepresentation
            The type of representation to generate, as a *class* (not an
            instance).

        in_frame_units : bool
            Force the representation units to match the specified units
            particular to this frame

        Returns
        -------
        newrep : whatever ``new_representation`` is
            A new representation object of this frame's `data`.

        Raises
        ------
        AttributeError
            If this object had no `data`

        Examples
        --------
        >>> from astropy import units as u
        >>> from astropy.coordinates import ICRS, CartesianRepresentation
        >>> coord = ICRS(0*u.deg, 0*u.deg)
        >>> coord.represent_as(CartesianRepresentation)
        <CartesianRepresentation x=1.0 , y=0.0 , z=0.0 >
        """
        cached_repr = self._rep_cache.get((new_representation.__name__, in_frame_units))
        if not cached_repr:
            data = self.data.represent_as(new_representation)

            # If the new representation is known to this frame and has a defined
            # set of names and units, then use that.
            new_attrs = self.representation_attrs.get(new_representation)
            if new_attrs and in_frame_units:
                datakwargs = dict((comp, getattr(data, comp)) for comp in data.components)
                for comp, new_attr_unit in zip(data.components, new_attrs['units']):
                    if new_attr_unit:
                        datakwargs[comp] = datakwargs[comp].to(new_attr_unit)
                data = data.__class__(**datakwargs)

            self._rep_cache[new_representation.__name__, in_frame_units] = data

        return self._rep_cache[new_representation.__name__, in_frame_units]

    def transform_to(self, new_frame):
        """
        Transform this object's coordinate data to a new frame.

        Parameters
        ----------
        new_frame : class or frame object or SkyCoord object
            The frame to transform this coordinate frame into.

        Returns
        -------
        transframe
            A new object with the coordinate data represented in the
            ``newframe`` system.

        Raises
        ------
        ValueError
            If there is no possible transformation route.
        """
        from .errors import ConvertError

        if self._data is None:
            raise ValueError('Cannot transform a frame with no data')

        if inspect.isclass(new_frame):
            #means use the defaults for this class
            new_frame = new_frame()

        if hasattr(new_frame, '_sky_coord_frame'):
            # Input new_frame is not a frame instance or class and is most
            # likely a SkyCoord object.
            new_frame = new_frame._sky_coord_frame

        trans = frame_transform_graph.get_transform(self.__class__,
                                                    new_frame.__class__)
        if trans is None:
            if new_frame is self.__class__:
                # no special transform needed, but should update frame info
                return new_frame.realize_frame(self.data)
            msg = 'Cannot transform from {0} to {1}'
            raise ConvertError(msg.format(self.__class__, new_frame.__class__))
        return trans(self, new_frame)

    def is_transformable_to(self, new_frame):
        """
        Determines if this coordinate frame can be transformed to another
        given frame.

        Parameters
        ----------
        new_frame : class or frame object
            The proposed frame to transform into.

        Returns
        -------
        transformable : bool or str
            `True` if this can be transformed to ``new_frame``, `False` if
            not, or the string 'same' if ``new_frame`` is the same system as
            this object but no transformation is defined.

        Notes
        -----
        A return value of 'same' means the transformation will work, but it will
        just give back a copy of this object.  The intended usage is::

            if coord.is_transformable_to(some_unknown_frame):
                coord2 = coord.transform_to(some_unknown_frame)

        This will work even if ``some_unknown_frame``  turns out to be the same
        frame class as ``coord``.  This is intended for cases where the frame is
        the same regardless of the frame attributes (e.g. ICRS), but be aware
        that it *might* also indicate that someone forgot to define the
        transformation between two objects of the same frame class but with
        different attributes.

        """

        new_frame_cls = new_frame if inspect.isclass(new_frame) else new_frame.__class__
        trans = frame_transform_graph.get_transform(self.__class__, new_frame_cls)

        if trans is None:
            if new_frame_cls is self.__class__:
                return 'same'
            else:
                return False
        else:
            return True

    def is_frame_attr_default(self, attrnm):
        """
        Determine whether or not a frame attribute has its value because it's
        the default value, or because this frame was created with that value
        explicitly requested.

        Parameters
        ----------
        attrnm : str
            The name of the attribute to check.

        Returns
        -------
        isdefault : bool
            True if the attribute ``attrnm`` has its value by default, False if it
            was specified at creation of this frame.
        """
        return attrnm in self._attr_names_with_defaults

    def __repr__(self):
        frameattrs = ', '.join([attrnm + '=' + str(getattr(self, attrnm))
                                for attrnm in self.frame_attr_names])

        if self.has_data:
            if self.representation:
                if (self.representation == SphericalRepresentation and
                        isinstance(self.data, UnitSphericalRepresentation)):
                    data = self.represent_as(UnitSphericalRepresentation, in_frame_units=True)
                else:
                    data = self.represent_as(self.representation, in_frame_units=True)

                data_repr = repr(data)
                for nmpref, nmrepr in self.representation_names.items():
                    data_repr = data_repr.replace(nmrepr, nmpref)

            else:
                data = self.data
                data_repr = repr(self.data)

            if data_repr.startswith('<' + data.__class__.__name__):
                # standard form from BaseRepresentation
                if frameattrs:
                    frameattrs = frameattrs + ', '

                #remove both the leading "<" and the space after the name
                data_repr = data_repr[(len(data.__class__.__name__) + 2):]

                return '<{0} Coordinate: {1}{2}'.format(self.__class__.__name__,
                                                        frameattrs, data_repr)
            else:
                # should only happen if a representation has a non-standard
                # __repr__ method, and we just punt to that
                if frameattrs:
                    frameattrs = ': ' + frameattrs + ', '
                s = '<{0} Coordinate{1}Data:\n{2}>'
                return s.format(self.__class__.__name__, frameattrs, data_repr)
        else:
            if frameattrs:
                frameattrs = ': ' + frameattrs
            return '<{0} Frame{1}>'.format(self.__class__.__name__, frameattrs)

    def __getitem__(self, view):
        if self.has_data:
            return self.realize_frame(self.data[view])
        else:
            raise ValueError('Cannot index a frame with no data')

    def __getattr__(self, attr):
        if attr not in self.representation_names:
            raise AttributeError("'{0}' object has no attribute '{1}'"
                                 .format(self.__class__.__name__, attr))

        rep = self.represent_as(self.representation, in_frame_units=True)
        val = getattr(rep, self.representation_names[attr])
        return val

    def __setattr__(self, attr, value):
        repr_attr_names = []
        if hasattr(self, 'representation_attrs'):
            for representation_attr in self.representation_attrs.values():
                repr_attr_names.extend(representation_attr['names'])
        if attr in repr_attr_names:
            raise AttributeError('Cannot set any frame attribute {0}'.format(attr))
        else:
            super(BaseCoordinateFrame, self).__setattr__(attr, value)

    def separation(self, other):
        """
        Computes on-sky separation between this coordinate and another.

        Parameters
        ----------
        other : `~astropy.coordinates.BaseCoordinateFrame`
            The coordinate to get the separation to.

        Returns
        -------
        sep : `~astropy.coordinates.Angle`
            The on-sky separation between this and the ``other`` coordinate.

        Notes
        -----
        The separation is calculated using the Vincenty formula, which
        is stable at all locations, including poles and antipodes [1]_.

        .. [1] http://en.wikipedia.org/wiki/Great-circle_distance

        """
        from .angle_utilities import angular_separation
        from .angles import Angle

        self_unit_sph = self.represent_as(UnitSphericalRepresentation)
        other_unit_sph = other.transform_to(self.__class__).represent_as(UnitSphericalRepresentation)

        # Get the separation as a Quantity, convert to Angle in degrees
        sep = angular_separation(self_unit_sph.lon, self_unit_sph.lat,
                                 other_unit_sph.lon, other_unit_sph.lat)
        return Angle(sep, unit=u.degree)

    def separation_3d(self, other):
        """
        Computes three dimensional separation between this coordinate
        and another.

        Parameters
        ----------
        other : `~astropy.coordinates.BaseCoordinateFrame`
            The coordinate system to get the distance to.

        Returns
        -------
        sep : `~astropy.coordinates.Distance`
            The real-space distance between these two coordinates.

        Raises
        ------
        ValueError
            If this or the other coordinate do not have distances.
        """
        from .distances import Distance

        if self.data.__class__ == UnitSphericalRepresentation:
            raise ValueError('This object does not have a distance; cannot '
                             'compute 3d separation.')

        # do this first just in case the conversion somehow creates a distance
        other_in_self_system = other.transform_to(self.__class__)

        if other_in_self_system.__class__ == UnitSphericalRepresentation:
            raise ValueError('The other object does not have a distance; '
                             'cannot compute 3d separation.')

        dx = self.cartesian.x - other_in_self_system.cartesian.x
        dy = self.cartesian.y - other_in_self_system.cartesian.y
        dz = self.cartesian.z - other_in_self_system.cartesian.z

        distval = (dx.value ** 2 + dy.value ** 2 + dz.value ** 2) ** 0.5
        return Distance(distval, dx.unit)

    @property
    def cartesian(self):
        """
        Shorthand for a cartesian representation of the coordinates in this object.
        """
        # TODO: if representations are updated to use a full transform graph,
        #       the representation aliases should not be hard-coded like this
        return self.represent_as(CartesianRepresentation, in_frame_units=True)

    @property
    def spherical(self):
        """
        Shorthand for a spherical representation of the coordinates in this object.
        """
        # TODO: if representations are updated to use a full transform graph,
        #       the representation aliases should not be hard-coded like this
        return self.represent_as(SphericalRepresentation, in_frame_units=True)


class GenericFrame(BaseCoordinateFrame):
    """
    A frame object that can't store data but can hold any arbitrary frame
    attributes. Mostly useful as a utility for the high-level class to store
    intermediate frame attributes.

    Parameters
    ----------
    frame_attrs : dict
        A dictionary of attributes to be used as the frame attributes for this
        frame.
    """
    name = None  # it's not a "real" frame so it doesn't have a name

    def __init__(self, frame_attrs):
        super(GenericFrame, self).__init__(None)

        self.frame_attr_names = frame_attrs
        for attrnm, attrval in frame_attrs.items():
            super(GenericFrame, self).__setattr__(attrnm, attrval)

    def __setattr__(self, name, value):
        if name in self.frame_attr_names:
            raise AttributeError("can't set frame attribute '{0}'".format(name))
        else:
            super(GenericFrame, self).__setattr__(name, value)


def _convert_to_time(attr, value):
    """
    Convert input value to a Time object and validate by running through the
    Time constructor.  Also check that the input was a scalar.
    """
    from ..time import Time
    try:
        out = Time(value)
    except Exception as err:
        raise ValueError('Invalid time input {0}={1!r}\n{2}'
                         .format(attr, value, err))

    if not out.isscalar:
        raise ValueError('Time input {0}={1!r} must be a single (scalar) value'
                         .format(attr, value))

    return out
